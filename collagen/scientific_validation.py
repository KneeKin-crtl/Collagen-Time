"""Offline reproducible evaluation CLI. Run ``python -m collagen.scientific_validation --help``."""
import argparse
from dataclasses import replace
from copy import deepcopy
import hashlib
import html
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import textwrap
import time

import numpy as np
import pandas as pd
import tifffile

from .feedback import fingerprint
from .loading import load_image
from .pipeline import analyze_pixels, calibrate_analysis
from .references import new_reference, validate_reference, split_groups, assign_group_splits
from .scientific_metrics import evaluate_instances, compare_methods
from .segmentation import ThresholdSegmenter
from .selection import SelectionFilters, select_baseline, apply_selection
from .grouping import image_group_components
from .visualization import display_rgb, count_overlay

BASELINE=dict(segmentation=dict(method='otsu',bright=False,threshold=.5,block_size=35,
                               opening_radius=0,closing_radius=0),sigma=0.,min_area=20,exclusion_px=2.)
CANDIDATE=deepcopy(BASELINE)
CANDIDATE['segmentation']['background_sigma_px']=25.
DEFAULT_PROFILES={'original':BASELINE,'candidate_conventional':CANDIDATE}


def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def prepare_dataset(images, output):
    output=Path(output).resolve();(output/'references').mkdir(parents=True,exist_ok=True)
    if (output/'dataset.json').exists():
        raise ValueError('A dataset manifest already exists. Do not overwrite annotation/split versions.')
    records=[];seen=set()
    for path in sorted(Path(images).resolve().iterdir()):
        if not path.is_file() or path.suffix.lower() not in ('.png','.jpg','.jpeg','.tif','.tiff'):continue
        image=load_image(path.read_bytes(),path.name)
        doc=new_reference(image,path.name)
        if doc['image_key'] in seen:
            raise ValueError('Duplicate original pixels found. Keep aliases in the same source record.')
        seen.add(doc['image_key'])
        refpath=Path('references')/(doc['image_key']+'.json')
        write_json(output/refpath,doc)
        records.append(dict(image=str(path),reference=str(refpath)))
    if not records:raise ValueError('No supported original single-plane images found.')
    manifest=dict(schema_version=1,seed=42,images=records)
    write_json(output/'dataset.json',manifest);write_json(output/'profiles.json',DEFAULT_PROFILES)
    return manifest


def load_dataset(manifest_path):
    path=Path(manifest_path).resolve();manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version')!=1 or not isinstance(manifest.get('images'),list):
        raise ValueError('Unsupported dataset manifest; expected schema_version 1 and images list.')
    records=[];keys=set();ids=set()
    for record in manifest['images']:
        image_path=(path.parent/record['image']).resolve()
        reference_path=(path.parent/record['reference']).resolve()
        doc=json.loads(reference_path.read_text(encoding='utf-8'));validate_reference(doc)
        if doc['image_key'] in keys or doc['image_id'] in ids:
            raise ValueError('Benchmark images need unique pixel identities and image IDs.')
        keys.add(doc['image_key']);ids.add(doc['image_id'])
        records.append(dict(image=image_path,reference=reference_path,document=doc))
    return manifest,records


def proposed_splits(manifest_path, output, seed=42):
    manifest,records=load_dataset(manifest_path)
    docs=assign_group_splits([r['document'] for r in records],seed)
    output=Path(output).resolve()
    if (output/'dataset.json').exists():raise ValueError('Write the split proposal to a new directory.')
    (output/'references').mkdir(parents=True,exist_ok=True)
    new_records=[]
    for record,doc in zip(records,docs):
        refpath=Path('references')/(doc['image_key']+'.json');write_json(output/refpath,doc)
        new_records.append(dict(image=str(record['image']),reference=str(refpath)))
    write_json(output/'dataset.json',dict(schema_version=1,seed=seed,images=new_records,
                                        split_policy='transitive specimen OR acquisition groups; no crop randomization'))


def read_checkpoint(path):
    from .learning import validate_model
    checkpoint=json.loads(Path(path).read_text(encoding='utf-8'))
    validate_model(checkpoint.get('payload',{}))
    if (checkpoint.get('approved') not in (True,1) or not isinstance(checkpoint.get('model_id'),str)
            or not checkpoint.get('report',{}).get('group_provenance')
            or not checkpoint.get('report',{}).get('group_registry')):
        raise ValueError('A scientific classifier comparison needs an approved checkpoint with frozen specimen/acquisition provenance.')
    return checkpoint


def validate_checkpoint_groups(checkpoint, documents, registry_documents=()):
    report=checkpoint['report'];provenance=report['group_provenance']
    for r in provenance:
        if not all(r.get(k) for k in ('image_key','specimen_id','acquisition_group')):
            raise ValueError('Checkpoint specimen/acquisition provenance is incomplete.')
    evaluated=list(documents)
    if any(not d.get('specimen_id') or not d.get('acquisition_group') for d in evaluated):
        raise ValueError('Verified groups are required before evaluating a classifier.')
    original_keys={r['image_key'] for r in provenance}
    if original_keys & {d['image_key'] for d in evaluated}:
        raise ValueError('Classifier evaluation overlaps a fitting/promotion image.')
    registry=report.get('group_registry') or provenance
    groups=image_group_components(list(registry)+provenance+list(registry_documents)+evaluated)
    used={groups[r['image_key']] for r in provenance}
    if used & {groups[d['image_key']] for d in evaluated}:
        raise ValueError('Classifier fitting/promotion and evaluation share a specimen/acquisition group.')


def automatic_analysis(image, doc, profile):
    allowed={'segmentation','sigma','min_area','exclusion_px','exclusion_regions','selection_filters','classifier_checkpoint'}
    if set(profile)-allowed:raise ValueError('Unknown profile settings; model/ground-truth fields cannot enter segmentation.')
    pixels=analyze_pixels(image,doc['image_id'],ThresholdSegmenter(**profile.get('segmentation',{})),
                         sigma=profile.get('sigma',0.),min_area=profile.get('min_area',20),
                         exclusion_px=profile.get('exclusion_px',2.),exclusion_regions=profile.get('exclusion_regions',()))
    calibration=doc.get('calibration_um_per_px')
    if calibration is None:
        if profile.get('classifier_checkpoint'):raise ValueError('Verified per-image calibration is required for classifier eligibility comparison.')
        return pixels
    analysis=calibrate_analysis(pixels,calibration,render=False)
    filters=SelectionFilters(**profile.get('selection_filters',{}))
    if not profile.get('classifier_checkpoint'):return select_baseline(analysis,filters)
    checkpoint=read_checkpoint(profile['classifier_checkpoint'])
    from .learning import object_features,predict
    from .preprocessing import normalize
    from scipy.ndimage import find_objects
    gray=normalize(image);boxes=find_objects(pixels.labels);features=[]
    for row in pixels.table.to_dict('records'):
        slices=boxes[int(row['fiber_id'])-1]
        features.append(object_features(pixels.labels[slices]==row['fiber_id'],gray[slices],row))
    predictions=predict(checkpoint['payload'],features)
    table=analysis.table.copy();table['automatic_classification']=np.where(predictions,'accepted','rejected')
    table['manual_classification']=None;table['decision_source']='model';table['model_version']=checkpoint['model_id']
    table=apply_selection(table,filters)
    selected=table[table.included_in_measurement_statistics]
    summary={**analysis.summary,'accepted_count':len(selected),'accepted_measurement_count':len(selected),
             'accepted_isolated_count':int((~selected.border_touch.astype(bool)).sum()),'review_required_count':len(table)-len(selected)}
    return replace(analysis,table=table,summary=summary,annotated=None,id_labels=None,parameters={**analysis.parameters,
        'selection_filters':filters.parameters(),'model_id':checkpoint['model_id'],
        'classifier_checkpoint_sha256':hashlib.sha256(Path(profile['classifier_checkpoint']).read_bytes()).hexdigest()})


def write_plots(evaluations, analyses, image, reference, output, image_token):
    """Publication-exportable files, using optional standard plotting tools."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .references import decode_reference_mask
    names=list(analyses)
    fig,axes=plt.subplots(1,len(names)+1,figsize=(6*(len(names)+1),5),squeeze=False)
    axes[0,0].imshow(display_rgb(image));axes[0,0].set_title('Original + independent reference outlines' if
        any(o['boundary_source']=='independent_manual' for o in reference['objects']) else 'Original — independent boundaries unavailable')
    for obj in reference['objects']:
        if obj['boundary_source']!='independent_manual':continue
        mask,(y0,x0,y1,x1)=decode_reference_mask(obj,image.shape)
        if min(mask.shape)>1:
            axes[0,0].contour(np.arange(x0-1,x1+1),np.arange(y0-1,y1+1),np.pad(mask,1),levels=[.5],colors=['#1557B0'],linewidths=.7)
    for ax,name in zip(axes[0,1:],names):
        a=analyses[name];ax.imshow(count_overlay(a.original,a.labels,a.table));ax.set_title(textwrap.fill(name+' — automatic QC, not biological truth',48),fontsize=10)
    for ax in axes[0]:ax.axis('off')
    fig.suptitle(reference['image_id']+' | source pixels retained; display scaled only')
    fig.tight_layout();fig.savefig(output/(image_token+'-examples.png'),dpi=140);plt.close(fig)
    for name,evaluation in evaluations.items():
        # Distinguish missed reference objects, poor-overlap boundaries and QC
        # mistakes; the program's ordinary red/green overlay is not error truth.
        analysis=analyses[name];error_rgb=display_rgb(image).copy()
        fp_ids=evaluation.prediction_rows.loc[evaluation.prediction_rows.false_positive.eq(True),'fiber_id']
        fp_mask=np.isin(analysis.labels,fp_ids)
        error_rgb[fp_mask]=(.4*error_rgb[fp_mask]+.6*np.array([210,0,180])).astype(np.uint8)
        error_fig,error_ax=plt.subplots(figsize=(10,7));error_ax.imshow(error_rgb)
        reference_lookup={o['object_id']:o for o in reference['objects']}
        for row in evaluation.reference_rows.itertuples():
            obj=reference_lookup[row.reference_id]
            color=('#00A6C7' if row.failure_mode=='no_overlap' else '#8F54E9' if not row.matched else
                   '#E49A20' if row.measurement_included!=(row.category=='valid_measurable_fiber') else '#19875B')
            mask,(y0,x0,y1,x1)=decode_reference_mask(obj,image.shape)
            error_ax.contour(np.arange(x0-1,x1+1),np.arange(y0-1,y1+1),np.pad(mask,1),levels=[.5],colors=[color],linewidths=1)
        error_ax.set_title(textwrap.fill(name+' | cyan: no overlap; purple: below-IoU overlap; orange: QC disagreement; magenta: known/scoped FP',85),fontsize=10)
        error_ax.axis('off');error_fig.tight_layout();error_fig.savefig(output/(image_token+'-'+name+'-errors.png'),dpi=140);plt.close(error_fig)
        if name!='original' and 'original' in evaluations:
            old=evaluations['original'].reference_rows
            now=evaluation.reference_rows
            previous=set(old.loc[old.category.isin(('valid_measurable_fiber','real_fiber_not_measurable'))&old.matched,'reference_id'])
            current=set(now.loc[now.category.isin(('valid_measurable_fiber','real_fiber_not_measurable'))&now.matched,'reference_id'])
            fig,ax=plt.subplots(figsize=(10,7));ax.imshow(display_rgb(image))
            for rid in sorted((current-previous)|(previous-current)):
                mask,(y0,x0,y1,x1)=decode_reference_mask(reference_lookup[rid],image.shape)
                ax.contour(np.arange(x0-1,x1+1),np.arange(y0-1,y1+1),np.pad(mask,1),levels=[.5],
                           colors=['#19875B' if rid in current else '#BE2838'],linewidths=1)
            ax.set_title(textwrap.fill(name+' vs original | green outline: recovered reference; red outline: lost reference; no outline means no such case',85),fontsize=10)
            ax.axis('off');fig.tight_layout();fig.savefig(output/(image_token+'-'+name+'-recovery.png'),dpi=140);plt.close(fig)
        pairs=evaluation.measurement_rows
        fig,axes=plt.subplots(2,2,figsize=(11,8))
        for col,metric in enumerate(('length_um','mean_diameter_um')):
            rows=pairs[pairs.metric.eq(metric)&pairs.paired]
            top,bottom=axes[:,col]
            if len(rows):
                top.hist(rows.signed_error_um,bins=min(12,max(1,len(rows))),edgecolor='white')
                bottom.scatter(rows.pair_mean_um,rows.signed_error_um,s=20)
                stats=evaluation.summary['measurement_agreement'][metric]['all_valid_reference_matches']
                bottom.axhline(stats['bias_um'],color='black',label='Bias')
                if stats['bland_altman_lower_um'] is not None:
                    for key in ('bland_altman_lower_um','bland_altman_upper_um'):
                        bottom.axhline(stats[key],color='#B32832',linestyle='--')
            else:
                for ax in (top,bottom):ax.text(.5,.5,'No independent usable measurement pairs',ha='center',va='center',transform=ax.transAxes)
            top.set_title(metric);top.set_xlabel('Automated − reference (µm)');top.set_ylabel('Paired objects')
            bottom.set_xlabel('Mean of automated and reference (µm)');bottom.set_ylabel('Automated − reference (µm)')
        fig.suptitle(textwrap.fill(name+' | Bland–Altman limits are descriptive ±1.96 sample SD; correlated fibers are not independent specimens',100),fontsize=10)
        fig.tight_layout();fig.savefig(output/(image_token+'-'+name+'-agreement.png'),dpi=140);plt.close(fig)


def evaluate_dataset(manifest_path, output, profiles=None, split='test', iou_threshold=.5, plots=True):
    manifest,records=load_dataset(manifest_path)
    profiles=deepcopy(profiles or DEFAULT_PROFILES)
    if 'original' not in profiles:raise ValueError('Keep an explicit original baseline profile.')
    if not np.isfinite(iou_threshold) or not 0<iou_threshold<=1:raise ValueError('Match IoU must be greater than zero and at most one.')
    contexts=[fingerprint({k:p.get(k,BASELINE.get(k)) for k in ('min_area','exclusion_px','exclusion_regions','selection_filters')})
              for p in profiles.values()]
    if len(set(contexts))!=1:
        raise ValueError('All methods must share measurement/QC definitions and exclusion regions for a fair comparison.')
    if any(not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name) for name in profiles):
        raise ValueError('Profile names may contain only letters, numbers, underscores and hyphens.')
    chosen=[r for r in records if r['document']['split']==split]
    if not chosen:raise ValueError(f'No images assigned to {split}. Complete the manifest or use --split unassigned for a technical inventory.')
    model_profiles=[name for name,p in profiles.items() if p.get('classifier_checkpoint')]
    for name in model_profiles:
        checkpoint=read_checkpoint(profiles[name]['classifier_checkpoint'])
        validate_checkpoint_groups(checkpoint,[r['document'] for r in chosen],[r['document'] for r in records])
    output=Path(output).resolve()
    if (output/'report.json').exists():raise ValueError('Report already exists. Use a new output directory to preserve the baseline.')
    output.mkdir(parents=True,exist_ok=True)
    (output/'reference-snapshots').mkdir(exist_ok=True)
    issues=[]
    try:split_groups([r['document'] for r in records])
    except ValueError as error:issues.append(str(error))
    if split=='unassigned':issues.append('Unassigned images provide a technical inventory, not an independent held-out evaluation.')
    if plots:
        os.environ.setdefault('MPLCONFIGDIR',str(output/'.matplotlib-cache'))
        os.environ.setdefault('XDG_CACHE_HOME',str(output/'.cache'))
        try:import matplotlib  # noqa: F401
        except ImportError as error:raise ValueError('Install the optional .[validation] extra for plots, or explicitly use --no-plots.') from error
    report=dict(schema_version=1,seed=manifest.get('seed',42),requested_split=split,
                held_out_test=split=='test' and not issues,readiness_issues=issues,
                manifest_sha256=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
                profile_sha256=fingerprint(profiles),profiles=profiles,
                runtime=dict(python=platform.python_version(),platform=platform.platform(),
                             packages={p:version(p) for p in ('numpy','scipy','scikit-image','pandas','tifffile','opencv-python-headless')+ (('matplotlib',) if plots else ())}),
                images=[],ml_model_status=('Frozen object classifier supplied; evaluated only where independent references exist. No segmentation model training.' if model_profiles else
                    'Not evaluated: no group-audited project checkpoint supplied; no new neural model trained.'),
                plot_status='generated' if plots else 'explicitly disabled',
                annotation_versions=[dict(image_key=r['document']['image_key'],version_id=r['document'].get('version_id'),
                    annotation_sha256=hashlib.sha256(r['reference'].read_bytes()).hexdigest()) for r in chosen])
    try:report['code_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).parents[1],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):report['code_commit']=None
    source_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    report['code_source_hashes']=source_files;report['code_fingerprint']=fingerprint(source_files)
    write_json(output/'dataset-snapshot.json',manifest)
    comparisons=[];summaries=[];all_evaluations={name:[] for name in profiles}
    for record in chosen:
        doc=record['document'];raw=record['image'].read_bytes();image=load_image(raw,record['image'].name)
        validate_reference(doc,image)
        write_json(output/'reference-snapshots'/(doc['image_key']+'.json'),doc)
        token=doc['image_key'][:16];analyses={};evaluations={};image_report=dict(image_id=doc['image_id'],image_key=doc['image_key'],
            source_sha256=hashlib.sha256(raw).hexdigest(),specimen_id=doc['specimen_id'],acquisition_group=doc['acquisition_group'],
            magnification=doc.get('magnification'),calibration_um_per_px=doc['calibration_um_per_px'],methods={})
        for name,profile in profiles.items():
            start=time.perf_counter();analysis=automatic_analysis(image,doc,profile);analyses[name]=analysis
            from scipy.ndimage import find_objects
            boxes=find_objects(analysis.labels)
            exported=analysis.table.copy()
            exported['bbox_yxyx']=[json.dumps([boxes[int(fid)-1][0].start,boxes[int(fid)-1][1].start,
                                              boxes[int(fid)-1][0].stop,boxes[int(fid)-1][1].stop]) for fid in exported.fiber_id]
            exported['source_image_key']=doc['image_key'];exported['specimen_id']=doc['specimen_id']
            exported['acquisition_group']=doc['acquisition_group'];exported['method']=name
            exported.to_csv(output/(token+'-'+name+'-predictions.csv'),index=False)
            tifffile.imwrite(output/(token+'-'+name+'-labels.tiff'),analysis.labels,compression='zlib')
            status=dict(candidates=len(analysis.table),qc_included=int(analysis.table.review_status.eq('accepted').sum()),
                        seconds=round(time.perf_counter()-start,4),parameters=analysis.parameters)
            independent=any(o['boundary_source']=='independent_manual' for o in doc['objects'])
            # Empty independent background is scoreable only when exhaustively
            # reviewed; copied masks alone cannot establish any boundary accuracy.
            independent=independent or (doc['coverage']=='exhaustive_regions' and not doc['objects'])
            if independent and not issues:
                evaluation=evaluate_instances(analysis,doc,iou_threshold);evaluations[name]=evaluation
                all_evaluations[name].append((doc,evaluation))
                status['evaluation']=evaluation.summary
                provenance=dict(image_id=doc['image_id'],source_image_key=doc['image_key'],specimen_id=doc['specimen_id'],method=name)
                evaluation.reference_rows.assign(**provenance).to_csv(output/(token+'-'+name+'-references.csv'),index=False)
                evaluation.prediction_rows.assign(**provenance).to_csv(output/(token+'-'+name+'-matches.csv'),index=False)
                evaluation.confusion.to_csv(output/(token+'-'+name+'-confusion.csv'))
                evaluation.measurement_rows.assign(**provenance).to_csv(output/(token+'-'+name+'-measurement-errors.csv'),index=False)
            else:
                status['evaluation']=None
                status['unavailable_reason']='Independent reference masks/coverage and verified group splits are required; prediction masks and binary corrections are not ground truth.'
            image_report['methods'][name]=status
            metrics=status.get('evaluation') or {};detection=metrics.get('detection',{})
            agreement=metrics.get('measurement_agreement',{}).get('length_um',{}).get('all_valid_reference_matches',{})
            summaries.append(dict(image_id=doc['image_id'],specimen_id=doc['specimen_id'],method=name,
                precision=detection.get('precision'),recall=detection.get('recall'),f1=detection.get('f1'),
                segmentation_iou=metrics.get('segmentation_iou'),length_mae_um=agreement.get('mean_absolute_error_um'),
                candidates=len(analysis.table),scientific_evaluation_available=status['evaluation'] is not None))
        if 'original' in evaluations:
            for name,evaluation in evaluations.items():
                if name=='original':continue
                comparisons.append(dict(image_id=doc['image_id'],method=name,**compare_methods(evaluations['original'],evaluation,
                    analyses['original'].labels,analyses[name].labels,iou_threshold)))
        if plots:write_plots(evaluations,analyses,image,doc,output,token)
        report['images'].append(image_report)
        write_json(output/'report.json',report)
    report['comparisons']=comparisons
    report['scientific_accuracy_status']='Quantitative reference results available within the stated scope; scientific acceptance tolerances still require agreement.' if any(s['scientific_evaluation_available'] for s in summaries) else 'Not established: independent ground truth / specimen metadata insufficient.'
    write_json(output/'report.json',report)
    frame=pd.DataFrame(summaries);frame.to_csv(output/'comparison.csv',index=False)
    pd.DataFrame(comparisons).to_csv(output/'recovered-and-new-false-positives.csv',index=False)
    metric_names=('Precision','Recall (annotated fibers)','F1','Segmentation IoU','Length MAE (µm)',
                  'Diameter MAE (µm)','Missed annotated fibers recovered','New false-positive regions')
    method_table=pd.DataFrame(index=metric_names,columns=list(profiles)+['ML model'])
    specimen_rows=[]
    for name,items in all_evaluations.items():
        if not items:continue
        complete=len(items)==len(chosen) and all(e.summary['coverage']=='independent_exhaustive_regions' for _,e in items)
        tp=sum(e.summary['detection']['true_positive'] for _,e in items)
        fn=sum(e.summary['detection']['false_negative'] for _,e in items)
        fp=sum(e.summary['detection']['false_positive'] or 0 for _,e in items)
        from .scientific_metrics import ratio, agreement_statistics
        method_table.loc['Precision',name]=ratio(tp,tp+fp) if complete else None
        method_table.loc['Recall (annotated fibers)',name]=ratio(tp,tp+fn)
        method_table.loc['F1',name]=ratio(2*tp,2*tp+fp+fn) if complete else None
        intersection=sum(e.summary['segmentation_intersection_pixels'] or 0 for _,e in items)
        union=sum(e.summary['segmentation_union_pixels'] or 0 for _,e in items)
        method_table.loc['Segmentation IoU',name]=ratio(intersection,union) if complete else None
        combined=pd.concat([e.measurement_rows for _,e in items],ignore_index=True)
        for metric,label in (('length_um','Length MAE (µm)'),('mean_diameter_um','Diameter MAE (µm)')):
            pairs=combined[combined.metric.eq(metric)]
            method_table.loc[label,name]=agreement_statistics(pairs.automated_um,pairs.reference_um)['mean_absolute_error_um']
        compared=[c for c in comparisons if c['method']==name]
        method_table.loc['Missed annotated fibers recovered',name]=0 if name=='original' else sum(c['missed_fibers_recovered'] for c in compared)
        method_table.loc['New false-positive regions',name]=(0 if name=='original' else sum(c['new_false_positives'] for c in compared)) if complete else None
        matrix=sum((e.confusion for _,e in items),start=items[0][1].confusion*0)
        matrix.to_csv(output/(name+'-confusion-total.csv'))
        for specimen in sorted({d['specimen_id'] for d,_ in items}):
            subset=[e for d,e in items if d['specimen_id']==specimen]
            p=sum(e.summary['detection']['true_positive'] for e in subset)
            n=sum(e.summary['detection']['false_negative'] for e in subset)
            f=sum(e.summary['detection']['false_positive'] or 0 for e in subset)
            exhaustive=all(e.summary['coverage']=='independent_exhaustive_regions' for e in subset)
            specimen_rows.append(dict(specimen_id=specimen,method=name,images=len(subset),
                precision=ratio(p,p+f) if exhaustive else None,recall=ratio(p,p+n),
                f1=ratio(2*p,2*p+f+n) if exhaustive else None,true_positive=p,false_negative=n,
                false_positive=f if exhaustive else None))
    if model_profiles:
        method_table=method_table.drop(columns='ML model').rename(columns={name:'ML model: '+name for name in model_profiles})
    else:method_table['ML model']='Not evaluated'
    method_table.index.name='Metric';method_table.to_csv(output/'method-summary.csv',na_rep='Unavailable')
    pd.DataFrame(specimen_rows).to_csv(output/'per-specimen-detection.csv',index=False)
    images=''.join(f'<figure><img src="{html.escape(p.name)}"><figcaption>{html.escape(p.stem)}</figcaption></figure>' for p in sorted(output.glob('*.png')))
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Independent fiber validation</title>
<style>body{font:15px system-ui;max-width:1200px;margin:30px auto;padding:12px;color:#18344a}img{max-width:100%}td,th{padding:7px;border:1px solid #ccc}table{border-collapse:collapse}pre{white-space:pre-wrap}</style>
<h1>Independent fiber validation report</h1>'''
    page+='<p>'+html.escape(report['scientific_accuracy_status'])+'</p>'
    page+='<p>Missing metrics are unavailable, not zero. Candidate-count changes do not demonstrate recovery. '+html.escape(report['ml_model_status'])+'</p>'
    page+='<h2>Readiness and scope</h2><pre>'+html.escape(json.dumps(issues,indent=2))+'</pre>'
    page+='<h2>Method summary</h2>'+method_table.to_html(na_rep='Unavailable')
    page+='<p>Detection rates pool object counts; IoU pools scoped pixels; MAE pools available paired fibers. These are descriptive summaries, not independent-specimen confidence intervals. Per-specimen counts are exported separately. Partial references cannot establish full precision, F1 or pixel IoU.</p>'
    page+='<h2>Same-image method comparison</h2>'+frame.to_html(index=False,na_rep='Unavailable')
    page+='<h2>Examples and agreement plots</h2>'+images
    page+='<p>See report.json, per-object matches, confusion and errors CSVs for provenance and failure cases. Bland–Altman limits are descriptive; fibers are correlated within specimens. No confidence intervals or treatment-effect claims are implied.</p></html>'
    (output/'report.html').write_text(page,encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    prepare=sub.add_parser('prepare',help='Create draft references; never turn predictions into truth.')
    prepare.add_argument('--images',type=Path,required=True);prepare.add_argument('--output',type=Path,required=True)
    split=sub.add_parser('split',help='Write a grouped split proposal in a new directory.')
    split.add_argument('--manifest',type=Path,required=True);split.add_argument('--output',type=Path,required=True)
    split.add_argument('--seed',type=int,default=42)
    run=sub.add_parser('run',help='Compare frozen methods on one declared split.')
    run.add_argument('--manifest',type=Path,required=True);run.add_argument('--output',type=Path,required=True)
    run.add_argument('--profiles',type=Path);run.add_argument('--split',choices=['unassigned','development','validation','test'],default='test')
    run.add_argument('--match-iou',type=float,default=.5);run.add_argument('--no-plots',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='prepare':prepare_dataset(args.images,args.output)
        elif args.command=='split':proposed_splits(args.manifest,args.output,args.seed)
        else:
            profiles=json.loads(args.profiles.read_text()) if args.profiles else None
            if profiles:
                for profile in profiles.values():
                    if profile.get('classifier_checkpoint'):
                        profile['classifier_checkpoint']=str((args.profiles.resolve().parent/profile['classifier_checkpoint']).resolve())
            result=evaluate_dataset(args.manifest,args.output,profiles,args.split,args.match_iou,not args.no_plots)
            print(result['scientific_accuracy_status'])
        print('Written:',args.output)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as error:parser.exit(2,str(error)+'\n')


if __name__=='__main__':main()
