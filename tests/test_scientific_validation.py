from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from collagen.feedback import FeedbackStore
from collagen.references import (new_reference,reference_object,decode_reference_mask,validate_reference,
                                  ReferenceStore,split_groups,assign_group_splits)
from collagen.scientific_metrics import (evaluate_instances,binary_metrics,agreement_statistics,
                                         match_instances,compare_methods,audit_saved_predictions)
from collagen.scientific_validation import evaluate_dataset,prepare_dataset,load_dataset
from collagen.segmentation import ThresholdSegmenter,segmentation_parameters


def fixture():
    shape=(100,220);labels=np.zeros(shape,np.int32)
    boxes={1:(10,10,20,60),2:(35,10,45,60),3:(10,90,20,110),4:(60,160,70,170),5:(70,10,80,30)}
    for fid,(y0,x0,y1,x1) in boxes.items():labels[y0:y1,x0:x1]=fid
    original=np.full(shape,80,np.uint8);original[labels>0]=200
    table=pd.DataFrame([dict(fiber_id=fid,review_status='accepted',length_um=10.,mean_diameter_um=2.,
                             included_in_measurement_statistics=True) for fid in boxes])
    analysis=SimpleNamespace(original=original,labels=labels,table=table,parameters={'calibration_um_per_px':.1})
    reference=new_reference(original,'synthetic.png',.1)
    reference.update(reviewer='independent synthetic reference',coverage='exhaustive_regions',
                     reviewed_regions_xyxy=[[0,0,220,100]],specimen_id='synthetic-specimen',
                     acquisition_group='synthetic-session',split='test',previously_used_for_development=False)
    for fid,category in [(1,'valid_measurable_fiber'),(2,'real_fiber_not_measurable'),(3,'non_fiber'),(4,'ambiguous')]:
        reference['objects'].append(reference_object(labels==fid,category,'independent synthetic reference',
            object_id=f'ref-{fid}',length_um=9. if fid==1 else None,mean_diameter_um=2.5 if fid==1 else None,
            measurement_source='analytically constructed synthetic reference'))
    missed=np.zeros(shape,bool);missed[35:45,90:140]=True
    reference['objects'].append(reference_object(missed,'valid_measurable_fiber','independent synthetic reference',object_id='missed'))
    return analysis,reference


def test_independent_detection_measurability_and_boundaries_are_separate():
    a,ref=fixture();before=a.labels.copy();table=a.table.copy()
    e=evaluate_instances(a,ref)
    d=e.summary['detection']
    assert (d['true_positive'],d['false_negative'],d['false_positive'])==(2,1,2)
    assert d['precision']==.5 and d['recall']==pytest.approx(2/3) and d['f1']==pytest.approx(4/7)
    assert d['false_negative_rate']==pytest.approx(1/3) and d['false_positive_rate'] is None
    assert e.summary['segmentation_iou']==pytest.approx(1000/1900)
    qc=e.summary['measurability_on_matched_references']
    assert qc['samples']==3 and qc['precision']==pytest.approx(1/3)
    assert qc['false_positive_rate']==1 and qc['false_negative_rate']==0
    assert e.summary['ignored_ambiguous_objects']==1
    assert e.confusion.loc['valid_measurable_fiber','measurement_included']==1
    assert e.confusion.loc['valid_measurable_fiber','not_detected']==1
    length=e.measurement_rows[e.measurement_rows.metric.eq('length_um')]
    assert length.iloc[0].signed_error_um==1 and length.iloc[0].absolute_relative_error_pct==pytest.approx(100/9)
    stats=e.summary['measurement_agreement']['length_um']['all_valid_reference_matches']
    assert stats['paired_count']==1 and stats['unavailable_pair_count']==1 and stats['median_absolute_error_um']==1
    assert stats['bland_altman_lower_um'] is None
    np.testing.assert_array_equal(a.labels,before);pd.testing.assert_frame_equal(a.table,table)


def test_partial_reference_does_not_turn_unlabeled_objects_into_false_positives():
    a,ref=fixture();ref['coverage']='partial';ref['reviewed_regions_xyxy']=[]
    e=evaluate_instances(a,ref)
    assert e.summary['detection']['false_positive'] is None
    assert e.summary['detection']['known_non_fiber_predictions']==1
    assert e.summary['detection']['precision'] is None and e.summary['detection']['f1'] is None
    assert e.summary['detection']['recall']==pytest.approx(2/3)
    assert e.summary['segmentation_iou'] is None
    assert e.prediction_rows.loc[e.prediction_rows.fiber_id.eq(5),'status'].iloc[0]=='unreviewed_unknown'


def test_copied_prediction_masks_are_not_independent_ground_truth():
    a,ref=fixture()
    for obj in ref['objects']:obj['boundary_source']='prediction_reference'
    e=evaluate_instances(a,ref)
    assert e.summary['independently_outlined_objects']==0
    assert e.summary['segmentation_iou'] is None and e.summary['detection']['precision'] is None
    assert e.summary['detection']['recall'] is None
    ref['objects'][0]['original_prediction']={'original_row':{'review_status':'accepted'}}
    matrix,metrics=audit_saved_predictions(ref)
    assert matrix.loc['valid_measurable_fiber','original_accepted']==1
    assert metrics['samples']==1 and metrics['precision']==1
    assert metrics['false_positive_rate'] is None


def test_empty_unreviewed_reference_never_reports_perfect_accuracy():
    a,ref=fixture();ref['objects']=[];ref['coverage']='partial';ref['reviewed_regions_xyxy']=[]
    e=evaluate_instances(a,ref)
    assert e.summary['detection']['precision'] is None and e.summary['detection']['recall'] is None
    assert e.summary['segmentation_iou'] is None
    assert e.summary['measurement_agreement']['length_um']['all_valid_reference_matches']['paired_count']==0
    assert binary_metrics([],[])['false_negative_rate'] is None


def test_exact_matching_maximizes_eligible_cardinality_instead_of_greedy_iou():
    assert set(match_instances([[.9,.6],[.6,0]],.5))=={(0,1),(1,0)}
    assert match_instances(np.zeros((0,4)))==[]
    with pytest.raises(ValueError):match_instances([[np.nan]])
    with pytest.raises(ValueError):match_instances([[1]],0)


def test_measurement_agreement_reports_missing_values_and_descriptive_limits():
    s=agreement_statistics([11,8,None],[10,10,5])
    assert s['paired_count']==2 and s['unavailable_pair_count']==1
    assert s['bias_um']==-.5 and s['mean_absolute_error_um']==1.5
    assert s['median_absolute_error_um']==1.5 and s['mean_absolute_relative_error_pct']==15
    assert s['bland_altman_lower_um']==pytest.approx(-.5-1.96*np.sqrt(4.5))
    assert agreement_statistics([None],[None])['mean_absolute_error_um'] is None


def test_recovery_uses_reference_identity_not_prediction_id_and_counts_new_fp_regions():
    before,ref=fixture();after=deepcopy(before)
    after.labels[35:45,90:140]=6
    after.labels[85:90,180:190]=7
    extra=pd.DataFrame([dict(fiber_id=i,review_status='accepted',length_um=10.,mean_diameter_um=2.,included_in_measurement_statistics=True) for i in (6,7)])
    after.table=pd.concat([after.table,extra],ignore_index=True)
    b=evaluate_instances(before,ref);a=evaluate_instances(after,ref)
    comparison=compare_methods(b,a,before.labels,after.labels)
    assert comparison['recovered_reference_ids']==['missed']
    assert comparison['missed_fibers_recovered']==1 and comparison['new_false_positives']==1
    ref['coverage']='partial';ref['reviewed_regions_xyxy']=[]
    assert compare_methods(evaluate_instances(before,ref),evaluate_instances(after,ref),before.labels,after.labels)['new_false_positives'] is None


def test_reference_versions_are_immutable_restorable_and_included_in_existing_backup(tmp_path):
    a,ref=fixture();feedback=FeedbackStore(tmp_path);store=ReferenceStore(tmp_path)
    v1=store.save(ref,a.original)
    v2=deepcopy(v1);v2['objects'][0]['category']='ambiguous';v2=store.save(v2,a.original)
    assert v2['parent_version_id']==v1['version_id'] and v2['annotation_revision']==2
    assert store.versions(ref['image_key'])[0]['objects'][0]['category']=='valid_measurable_fiber'
    assert ReferenceStore(tmp_path).load(ref['image_key'])==v2
    with pytest.raises(ValueError,match='changed'):store.save(v1,a.original)
    import sqlite3
    backup=tmp_path/'backup.sqlite3';backup.write_bytes(feedback.backup())
    with sqlite3.connect(backup) as con:assert con.execute('SELECT COUNT(*) FROM reference_versions').fetchone()[0]==2
    bad=a.original.copy();bad[0,0]+=1
    with pytest.raises(ValueError,match='match'):validate_reference(v2,bad)


@pytest.mark.parametrize('field,value',[('category','accepted'),('reviewer',''),('length_um',-1),('length_um',float('inf'))])
def test_invalid_reference_objects_are_rejected(field,value):
    a,ref=fixture();ref['objects'][0][field]=value
    with pytest.raises(ValueError):validate_reference(ref,a.original)


def test_mask_coordinates_and_corrupt_compression_are_checked():
    a,ref=fixture();mask,bbox=decode_reference_mask(ref['objects'][0],a.labels.shape)
    assert bbox==(10,10,20,60) and mask.all()
    ref['objects'][0]['bbox_yxyx']=[0,0,1000,1000]
    with pytest.raises(ValueError,match='outside'):validate_reference(ref)
    a,ref=fixture();ref['objects'][0]['mask_data']='not base64!'
    with pytest.raises(ValueError,match='mask data'):validate_reference(ref)
    a,ref=fixture();ref['objects'].append(deepcopy(ref['objects'][0]))
    with pytest.raises(ValueError,match='unique'):validate_reference(ref)


def documents():
    docs=[]
    for i in range(5):
        image=np.full((10,12),i,np.uint8)
        d=new_reference(image,f'image-{i}.png',.1)
        d.update(specimen_id=f'specimen-{i}',acquisition_group=f'acquisition-{i}',previously_used_for_development=False)
        docs.append(d)
    return docs


def test_group_splits_are_seeded_transitive_and_do_not_leak_acquisition_or_specimen():
    docs=documents();one=assign_group_splits(docs,42);two=assign_group_splits(docs,42)
    assert [d['split'] for d in one]==[d['split'] for d in two]
    assert set(d['split'] for d in one)=={'development','validation','test'}
    docs[0]['split']='development';docs[2]['split']='test'
    docs[1]['specimen_id']=docs[0]['specimen_id']
    docs[1]['acquisition_group']=docs[2]['acquisition_group']
    with pytest.raises(ValueError,match='leakage'):split_groups(docs)
    docs=documents();docs[0]['previously_used_for_development']=True;docs[0]['split']='test'
    with pytest.raises(ValueError,match='Previously'):split_groups(docs)
    with pytest.raises(ValueError,match='Duplicate'):split_groups([documents()[0],documents()[0]])


def test_conventional_recovery_is_opt_in_and_does_not_connect_isolated_weak_debris():
    image=np.zeros((40,100),float)
    image[15:20,10:35]=.9;image[15:20,35:70]=.3;image[28:31,80:90]=.3
    recovered=ThresholdSegmenter(method='hysteresis',low_threshold=.2,high_threshold=.7)(image)
    assert recovered[17,50] and not recovered[29,85]
    assert not ThresholdSegmenter(method='manual',threshold=.7)(image)[17,50]
    dark=1-image
    assert np.array_equal(recovered,ThresholdSegmenter(method='hysteresis',bright=False,low_threshold=.2,high_threshold=.7)(dark))
    assert segmentation_parameters(ThresholdSegmenter())==dict(method='otsu',bright=True,threshold=.5,block_size=35,opening_radius=0,closing_radius=0)
    with pytest.raises(ValueError):ThresholdSegmenter(method='hysteresis',low_threshold=.7,high_threshold=.2)(image)
    assert not ThresholdSegmenter(background_sigma_px=10)(np.ones((20,20))).any()


def test_report_without_annotations_has_no_invented_scores_and_cannot_overwrite(tmp_path):
    originals=tmp_path/'originals';originals.mkdir()
    image=np.full((80,120),80,np.uint8);image[20:29,20:100]=200
    Image.fromarray(image).save(originals/'original.png')
    prepare_dataset(originals,tmp_path/'dataset')
    manifest=tmp_path/'dataset/dataset.json'
    profiles={'original':dict(segmentation=dict(bright=True)), 'candidate_conventional':dict(segmentation=dict(bright=True,background_sigma_px=12))}
    report=evaluate_dataset(manifest,tmp_path/'report',profiles,split='unassigned',plots=False)
    assert 'Not established' in report['scientific_accuracy_status']
    assert not report['held_out_test'] and report['images'][0]['methods']['original']['evaluation'] is None
    summary=pd.read_csv(tmp_path/'report/method-summary.csv',keep_default_na=False)
    assert set(summary.original)=={'Unavailable'} and set(summary['ML model'])=={'Not evaluated'}
    assert (tmp_path/'report/report.html').is_file()
    assert (tmp_path/'report/reference-snapshots').is_dir()
    with pytest.raises(ValueError,match='already exists'):
        evaluate_dataset(manifest,tmp_path/'report',profiles,split='unassigned',plots=False)
    profiles['candidate_conventional']['min_area']=50
    with pytest.raises(ValueError,match='share measurement'):
        evaluate_dataset(manifest,tmp_path/'other',profiles,split='unassigned',plots=False)
