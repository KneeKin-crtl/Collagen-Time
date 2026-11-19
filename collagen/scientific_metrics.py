"""Independent object, pixel and measurement evaluation, never prediction labels as truth."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .references import CATEGORIES, decode_reference_mask, validate_reference

FIBER_CATEGORIES = ('valid_measurable_fiber','real_fiber_not_measurable')


def ratio(numerator, denominator):
    return float(numerator/denominator) if denominator else None


def binary_metrics(truth, predicted):
    truth=np.asarray(truth);predicted=np.asarray(predicted)
    for values in (truth,predicted):
        if values.dtype.kind not in 'biuf' or not np.isfinite(values).all() or not np.isin(values,[0,1]).all():
            raise ValueError('Use explicit boolean/zero-one decisions, not status strings or missing labels.')
    truth=truth.astype(bool);predicted=predicted.astype(bool)
    if truth.shape!=predicted.shape or truth.ndim!=1:
        raise ValueError('Binary evaluation needs paired one-dimensional decisions.')
    tp=int((truth & predicted).sum());fp=int((~truth & predicted).sum())
    fn=int((truth & ~predicted).sum());tn=int((~truth & ~predicted).sum())
    return dict(samples=len(truth),true_positive=tp,false_positive=fp,false_negative=fn,true_negative=tn,
                precision=ratio(tp,tp+fp),recall=ratio(tp,tp+fn),f1=ratio(2*tp,2*tp+fp+fn),
                false_positive_rate=ratio(fp,fp+tn),false_negative_rate=ratio(fn,fn+tp))


def agreement_statistics(automated, reference):
    """Independent per-metric pairs; normal-theory limits are descriptive, not CIs."""
    a=np.asarray(automated,float);r=np.asarray(reference,float)
    if a.shape!=r.shape or a.ndim!=1:
        raise ValueError('Measurement agreement requires paired one-dimensional values.')
    usable=np.isfinite(a)&np.isfinite(r)&(a>0)&(r>0)
    error=a[usable]-r[usable];absolute=np.abs(error)
    spread=float(error.std(ddof=1)) if len(error)>1 else None
    bias=float(error.mean()) if len(error) else None
    return dict(paired_count=len(error),unavailable_pair_count=int((~usable).sum()),
                bias_um=bias,mean_absolute_error_um=float(absolute.mean()) if len(error) else None,
                median_absolute_error_um=float(np.median(absolute)) if len(error) else None,
                mean_absolute_relative_error_pct=float((100*absolute/r[usable]).mean()) if len(error) else None,
                difference_sd_um=spread,
                bland_altman_lower_um=bias-1.96*spread if spread is not None else None,
                bland_altman_upper_um=bias+1.96*spread if spread is not None else None)


def audit_saved_predictions(reference):
    """Human category vs the immutable original QC label, not mask accuracy.

    This selected-candidate audit can use classification-only linked references.
    It cannot estimate missed-object recall, boundary IoU or four-way model accuracy.
    """
    validate_reference(reference)
    matrix=pd.DataFrame(0,index=pd.Index(CATEGORIES,name='human_reference'),
                        columns=['original_accepted','original_review_required','no_original_prediction'])
    truth=[];predictions=[]
    for obj in reference['objects']:
        row=(obj.get('original_prediction') or {}).get('original_row',{})
        status=row.get('review_status')
        column='original_accepted' if status=='accepted' else 'original_review_required' if status=='review_required' else 'no_original_prediction'
        matrix.loc[obj['category'],column]+=1
        if status in ('accepted','review_required') and obj['category']!='ambiguous':
            truth.append(obj['category']=='valid_measurable_fiber');predictions.append(status=='accepted')
    return matrix,binary_metrics(truth,predictions)


def match_instances(ious, threshold=.5):
    """Maximum eligible match cardinality, then IoU; one-to-one, deterministic."""
    ious=np.asarray(ious,float)
    if ious.ndim!=2 or not np.isfinite(ious).all() or np.any((ious<0)|(ious>1)):
        raise ValueError('Instance IoUs must form a finite matrix between zero and one.')
    if not np.isfinite(threshold) or not 0<threshold<=1:
        raise ValueError('Matching IoU threshold must be greater than zero and at most one.')
    if not all(ious.shape):return []
    eligible=ious>=threshold
    weights=np.where(eligible,min(ious.shape)+1+ious,0.)
    rows,cols=linear_sum_assignment(-weights)
    return [(int(i),int(j)) for i,j in zip(rows,cols) if eligible[i,j]]


@dataclass
class ScientificEvaluation:
    summary: dict
    reference_rows: pd.DataFrame
    prediction_rows: pd.DataFrame
    confusion: pd.DataFrame
    measurement_rows: pd.DataFrame
    scoring_mask: np.ndarray


def evaluate_instances(analysis, reference, iou_threshold=.5):
    """Score a frozen automatic result against explicitly independent references.

    On partial annotations, unmatched predictions are unknown, not false positives;
    global precision/F1/pixel IoU are unavailable. Pixel scoring can clip to a
    reviewed ROI; source masks, IDs and geometric measurements are never clipped.
    """
    validate_reference(reference,analysis.original)
    expected=reference.get('calibration_um_per_px')
    actual=analysis.parameters.get('calibration_um_per_px')
    if expected is not None and actual is not None and not np.isclose(expected,actual,rtol=1e-9,atol=0):
        raise ValueError('Analysis and independent reference calibration differ; compare equivalent physical units.')
    labels=np.asarray(analysis.labels)
    if labels.ndim!=2 or labels.shape!=analysis.original.shape[:2] or labels.dtype.kind not in 'iu':
        raise ValueError('Predictions need original-resolution nonnegative integer instance labels.')
    if np.any(labels<0):raise ValueError('Negative prediction labels are invalid.')
    table=analysis.table.copy()
    if table.fiber_id.duplicated().any() or set(map(int,np.unique(labels)))-{0}!=set(map(int,table.fiber_id)):
        raise ValueError('Every nonzero predicted instance must have exactly one measurement row.')
    rows_by_id=table.set_index('fiber_id')
    scope=np.zeros(labels.shape,bool)
    if reference['coverage']=='partial':scope[:]=True
    else:
        for x0,y0,x1,y1 in reference['reviewed_regions_xyxy']:scope[y0:y1,x0:x1]=True
    for x0,y0,x1,y1 in analysis.parameters.get('exclusion_regions_xyxy',[]):scope[y0:y1,x0:x1]=False
    refs=[];copied=[];ambiguous_count=0
    for obj in reference['objects']:
        mask,bbox=decode_reference_mask(obj,labels.shape)
        y0,x0,y1,x1=bbox
        if obj['boundary_source']!='independent_manual':
            copied.append(obj);continue
        if obj['category']=='ambiguous':
            scope[y0:y1,x0:x1]&=~mask;ambiguous_count+=1
        else:refs.append((obj,mask,bbox))
    # Prediction-derived masks cannot fill missing independent boundary coverage.
    copied_in_scope=0
    for obj in copied:
        mask,(y0,x0,y1,x1)=decode_reference_mask(obj,labels.shape)
        copied_in_scope+=int(np.any(mask & scope[y0:y1,x0:x1]))
    complete=reference['coverage']=='exhaustive_regions' and not copied_in_scope and bool(scope.any())
    pred_ids=np.unique(labels[scope]);pred_ids=pred_ids[pred_ids!=0]
    pred_areas=np.bincount(labels[scope].ravel(),minlength=int(labels.max(initial=0))+1)
    truth_fibers=np.zeros(labels.shape,bool);ious=[];scorable=[]
    for obj,mask,bbox in refs:
        y0,x0,y1,x1=bbox;scoped=mask & scope[y0:y1,x0:x1]
        if not scoped.any():continue
        if obj['category'] in FIBER_CATEGORIES:truth_fibers[y0:y1,x0:x1]|=scoped
        counts=np.bincount(labels[y0:y1,x0:x1][scoped],minlength=len(pred_areas))
        union=int(scoped.sum())+pred_areas[pred_ids]-counts[pred_ids]
        ious.append(np.divide(counts[pred_ids],union,out=np.zeros(len(pred_ids),float),where=union>0))
        scorable.append(obj)
    ious=np.asarray(ious,float).reshape(len(scorable),len(pred_ids))
    matched=dict(match_instances(ious,iou_threshold));reverse={j:i for i,j in matched.items()}
    prediction_columns=('measurement_included','review_required','not_detected')
    confusion=pd.DataFrame(0,index=pd.Index(CATEGORIES,name='human_reference'),columns=prediction_columns)
    ref_rows=[];pred_rows=[];measurement_rows=[];truth_binary=[];pred_binary=[]
    for i,obj in enumerate(scorable):
        j=matched.get(i);fid=int(pred_ids[j]) if j is not None else None
        row=rows_by_id.loc[fid] if fid is not None else None
        included=bool(row.get('included_in_measurement_statistics',row.review_status=='accepted')) if row is not None else False
        decision='not_detected' if row is None else 'measurement_included' if included else 'review_required'
        confusion.loc[obj['category'],decision]+=1
        if row is not None:
            truth_binary.append(obj['category']=='valid_measurable_fiber');pred_binary.append(included)
        best=float(ious[i].max()) if len(pred_ids) else 0.
        failure=('matched' if j is not None else 'overlap_below_matching_threshold' if best>0 else 'no_overlap')
        ref_rows.append(dict(reference_id=obj['object_id'],category=obj['category'],fiber_id=fid,
                             matched=j is not None,iou=float(ious[i,j]) if j is not None else None,
                             best_overlap_iou=best,measurement_included=included,failure_mode=failure,
                             overlapping_prediction_count=int((ious[i]>0).sum())))
        if obj['category']=='valid_measurable_fiber':
            for metric in ('length_um','mean_diameter_um'):
                a=float(row.get(metric,np.nan)) if row is not None else np.nan
                r=obj.get(metric);r=float(r) if r is not None else np.nan
                ok=np.isfinite([a,r]).all() and a>0 and r>0
                measurement_rows.append(dict(reference_id=obj['object_id'],fiber_id=fid,metric=metric,
                    automated_um=a if np.isfinite(a) else None,reference_um=r if np.isfinite(r) else None,
                    measurement_included=included,paired=bool(ok),
                    signed_error_um=float(a-r) if ok else None,absolute_error_um=float(abs(a-r)) if ok else None,
                    relative_error_pct=float(100*(a-r)/r) if ok else None,
                    absolute_relative_error_pct=float(100*abs(a-r)/r) if ok else None,
                    pair_mean_um=float((a+r)/2) if ok else None))
    for j,fid in enumerate(pred_ids):
        i=reverse.get(j);category=scorable[i]['category'] if i is not None else None
        fp=category=='non_fiber' if i is not None else True if complete else None
        pred_rows.append(dict(fiber_id=int(fid),reference_id=scorable[i]['object_id'] if i is not None else None,
                              reference_category=category,false_positive=fp,
                              status='fiber_match' if category in FIBER_CATEGORIES else 'known_non_fiber' if category=='non_fiber'
                              else 'unmatched_false_positive' if complete else 'unreviewed_unknown',
                              overlapping_reference_count=int((ious[:,j]>0).sum())))
    fibers=[i for i,o in enumerate(scorable) if o['category'] in FIBER_CATEGORIES]
    tp=sum(i in matched for i in fibers);fn=len(fibers)-tp
    known_fp=sum(r['status']=='known_non_fiber' for r in pred_rows)
    fp=sum(r['false_positive'] is True for r in pred_rows) if complete else None
    foreground=(labels>0)&scope;union=int((foreground|truth_fibers).sum());intersection=int((foreground&truth_fibers).sum())
    measured=pd.DataFrame(measurement_rows,columns=['reference_id','fiber_id','metric','automated_um','reference_um',
        'measurement_included','paired','signed_error_um','absolute_error_um','relative_error_pct','absolute_relative_error_pct','pair_mean_um'])
    agreement={}
    for metric in ('length_um','mean_diameter_um'):
        selected=measured[measured.metric.eq(metric)]
        agreement[metric]=dict(all_valid_reference_matches=agreement_statistics(selected.automated_um,selected.reference_um),
            operationally_included=agreement_statistics(selected.loc[selected.measurement_included,'automated_um'],
                                                        selected.loc[selected.measurement_included,'reference_um']))
    summary=dict(coverage='independent_exhaustive_regions' if complete else 'partial_or_unverified_boundaries',
        matching_iou_threshold=float(iou_threshold),scored_pixels=int(scope.sum()),
        independently_outlined_objects=len(scorable),classification_only_copied_masks=len(copied),ignored_ambiguous_objects=ambiguous_count,
        unverified_boundary_objects_in_scoring_scope=copied_in_scope,
        predicted_objects=len(pred_ids),detection=dict(true_positive=tp,false_negative=fn,false_positive=fp,
            known_non_fiber_predictions=known_fp,unreviewed_predictions=sum(r['false_positive'] is None for r in pred_rows),
            precision=ratio(tp,tp+fp) if fp is not None else None,recall=ratio(tp,tp+fn),
            f1=ratio(2*tp,2*tp+fp+fn) if fp is not None else None,
            false_negative_rate=ratio(fn,tp+fn),false_positive_rate=None,
            false_positive_rate_reason='Object detection has no defined population of true-negative background objects; use conditional QC FPR below.'),
        segmentation_iou=ratio(intersection,union) if complete else None,
        segmentation_intersection_pixels=intersection if complete else None,
        segmentation_union_pixels=union if complete else None,
        segmentation_iou_reason=None if complete and union else 'Independent exhaustive foreground/background coverage with positive union is required.',
        measurability_on_matched_references=binary_metrics(truth_binary,pred_binary),
        measurement_agreement=agreement,
        four_way_prediction_status='Baseline predicts measurement inclusion/review, not four biological classes; matrix columns retain those actual decisions.',
        missing_ground_truth_reason=None if complete else 'Complete independent region annotations are absent; unmatched predictions are not presumed false positives.')
    return ScientificEvaluation(summary,pd.DataFrame(ref_rows,columns=['reference_id','category','fiber_id','matched','iou',
        'best_overlap_iou','measurement_included','failure_mode','overlapping_prediction_count']),
        pd.DataFrame(pred_rows,columns=['fiber_id','reference_id','reference_category','false_positive','status','overlapping_reference_count']),confusion,measured,scope)


def compare_methods(before, after, before_labels, after_labels, iou_threshold=.5):
    """Recovery is independent-reference identity; new FPs use spatial matching."""
    if (not np.array_equal(before.scoring_mask,after.scoring_mask)
            or before_labels.shape!=before.scoring_mask.shape or after_labels.shape!=after.scoring_mask.shape):
        raise ValueError('Compare methods within the same independently reviewed pixel scope.')
    def matched_fibers(report):
        r=report.reference_rows
        return set(r.loc[r.category.isin(FIBER_CATEGORIES)&r.matched,'reference_id'])
    old,new=matched_fibers(before),matched_fibers(after)
    comparison=dict(recovered_reference_ids=sorted(new-old),lost_reference_ids=sorted(old-new),
                    missed_fibers_recovered=len(new-old),new_false_positives=None)
    def eligible_valid(report):
        rows=report.reference_rows
        return set(rows.loc[rows.category.eq('valid_measurable_fiber')&rows.matched&rows.measurement_included,'reference_id'])
    previous_eligible,current_eligible=eligible_valid(before),eligible_valid(after)
    comparison['newly_eligible_valid_reference_ids']=sorted(current_eligible-previous_eligible)
    comparison['lost_eligible_valid_reference_ids']=sorted(previous_eligible-current_eligible)
    comparison['measurement_accuracy_improvement']=None
    comparison['measurement_accuracy_note']='Eligibility is not agreement. Review paired errors against prospectively defined scientific tolerances.'
    if before.summary['coverage']==after.summary['coverage']=='independent_exhaustive_regions':
        old_ids=before.prediction_rows.loc[before.prediction_rows.false_positive.eq(True),'fiber_id'].tolist()
        new_ids=after.prediction_rows.loc[after.prediction_rows.false_positive.eq(True),'fiber_id'].tolist()
        overlap=np.zeros((len(old_ids),len(new_ids)),float)
        new_areas={fid:int(((after_labels==fid)&after.scoring_mask).sum()) for fid in new_ids}
        for i,fid in enumerate(old_ids):
            mask=(before_labels==fid)&before.scoring_mask;values,counts=np.unique(after_labels[mask],return_counts=True)
            intersections=dict(zip(values,counts));area=int(mask.sum())
            for j,new_id in enumerate(new_ids):
                intersection=int(intersections.get(new_id,0));overlap[i,j]=intersection/(area+new_areas[new_id]-intersection)
        comparison['new_false_positives']=len(new_ids)-len(match_instances(overlap,iou_threshold))
        comparison['new_false_positive_definition']='New FP regions unmatched to baseline FP regions at the stated one-to-one IoU threshold; not merely a count difference.'
    return comparison
