from copy import deepcopy
import json

import numpy as np
import pandas as pd
from PIL import Image
import pytest

from collagen.references import new_reference,reference_object
from collagen.scientific_validation import evaluate_dataset,validate_checkpoint_groups,automatic_analysis
from test_learning import reviewed_dataset


def dataset(tmp_path):
    records=[]
    for i,split in enumerate(('development','validation','test')):
        image=np.full((60,120),80,np.uint8);image[0,0]=77+i
        mask=np.zeros(image.shape,bool);mask[20:25,20:90]=True;image[mask]=200
        path=tmp_path/f'synthetic-{i}.png';Image.fromarray(image).save(path)
        doc=new_reference(image,path.name,.1)
        doc.update(specimen_id=f'synthetic-specimen-{i}',acquisition_group=f'synthetic-session-{i}',
                   split=split,previously_used_for_development=False,reviewer='analytical synthetic truth',
                   coverage='exhaustive_regions',reviewed_regions_xyxy=[[0,0,120,60]])
        doc['objects']=[reference_object(mask,'valid_measurable_fiber','analytical synthetic truth',
            length_um=6.5,mean_diameter_um=.5,measurement_source='analytic synthetic dimensions')]
        reference=tmp_path/f'reference-{i}.json';reference.write_text(json.dumps(doc))
        records.append(dict(image=str(path),reference=str(reference)))
    manifest=tmp_path/'dataset.json';manifest.write_text(json.dumps(dict(schema_version=1,seed=42,images=records)))
    return manifest


def test_scientific_report_scores_independent_instances_and_freezes_sources(tmp_path):
    manifest=dataset(tmp_path)
    profiles={'original':dict(segmentation=dict(bright=True)),
              'candidate_conventional':dict(segmentation=dict(bright=True,background_sigma_px=10))}
    report=evaluate_dataset(manifest,tmp_path/'report',profiles,plots=False)
    assert report['held_out_test']
    assert report['images'][0]['methods']['original']['evaluation']['detection']['precision']==1
    assert report['comparisons'][0]['missed_fibers_recovered']==0
    assert report['comparisons'][0]['new_false_positives']==0
    assert report['code_fingerprint'] and report['annotation_versions'][0]['annotation_sha256']
    summary=pd.read_csv(tmp_path/'report/method-summary.csv',index_col=0)
    assert float(summary.loc['Precision','original'])==1
    assert summary.loc['Length MAE (µm)','original'] is not None
    assert (tmp_path/'report/per-specimen-detection.csv').is_file()
    predicted=next((tmp_path/'report').glob('*-original-predictions.csv'))
    frame=pd.read_csv(predicted)
    assert frame.bbox_yxyx.iloc[0]=='[20, 20, 25, 90]'
    assert frame.source_image_key.iloc[0]==report['images'][0]['image_key']


def test_matplotlib_exports_agreement_failure_and_recovery_figures(tmp_path,monkeypatch):
    pytest.importorskip('matplotlib')
    monkeypatch.setenv('MPLCONFIGDIR',str(tmp_path/'mpl'))
    monkeypatch.setenv('XDG_CACHE_HOME',str(tmp_path/'cache'))
    manifest=dataset(tmp_path)
    profiles={'original':dict(segmentation=dict(bright=True)),
              'candidate_conventional':dict(segmentation=dict(bright=True,background_sigma_px=10))}
    report=evaluate_dataset(manifest,tmp_path/'report',profiles,plots=True)
    for suffix in ('-examples.png','-original-agreement.png','-original-errors.png','-candidate_conventional-recovery.png'):
        files=list((tmp_path/'report').glob('*'+suffix));assert len(files)==1
        with Image.open(files[0]) as image:assert image.width>500 and image.height>300
    assert report['plot_status']=='generated'


def test_group_audited_checkpoint_can_be_inferred_without_changing_masks_or_geometry(reviewed_dataset,tmp_path):
    from collagen.learning import retrain
    store,sessions=reviewed_dataset;retrain(store)
    checkpoint=store.active_model();path=tmp_path/'classifier.json';path.write_text(json.dumps(checkpoint))
    image=sessions[0].baseline.original.copy();image[0,0]=12
    doc=new_reference(image,'new-group.png',.25)
    doc.update(specimen_id='unseen-specimen',acquisition_group='unseen-session')
    validate_checkpoint_groups(checkpoint,[doc])
    profile=dict(segmentation=dict(bright=True),min_area=1000)
    baseline=automatic_analysis(image,doc,profile)
    after=automatic_analysis(image,doc,{**profile,'classifier_checkpoint':str(path)})
    np.testing.assert_array_equal(baseline.labels,after.labels)
    for field in ('fiber_id','length_um','mean_diameter_um','diameter_sd_um'):
        pd.testing.assert_series_equal(baseline.table[field],after.table[field])
    assert 'classifier_checkpoint_sha256' in after.parameters
    assert set(after.table.decision_source)=={'model'}
    assert store.active_model()['model_id']==checkpoint['model_id']
    doc['specimen_id']=checkpoint['report']['group_provenance'][0]['specimen_id']
    with pytest.raises(ValueError,match='share a specimen'):validate_checkpoint_groups(checkpoint,[doc])


def test_checkpoint_cannot_use_a_transitive_unassigned_group_bridge(reviewed_dataset):
    from collagen.learning import retrain
    store,_=reviewed_dataset;retrain(store);checkpoint=store.active_model()
    doc=new_reference(np.full((30,40),12,np.uint8),'new.png',.1)
    doc.update(specimen_id='unseen-specimen',acquisition_group='unseen-acquisition')
    bridge=new_reference(np.full((30,40),13,np.uint8),'bridge.png')
    bridge.update(specimen_id=checkpoint['report']['group_provenance'][0]['specimen_id'],
                  acquisition_group=doc['acquisition_group'])
    with pytest.raises(ValueError,match='share a specimen'):
        validate_checkpoint_groups(checkpoint,[doc],[bridge,doc])
