import json

import cv2
import numpy as np
import pytest

from collagen.feedback import FeedbackStore
from collagen.learning import FEATURE_NAMES, classification_metrics, predict, retrain
from collagen.pipeline import analyze
from collagen.review import ReviewSession


@pytest.fixture
def reviewed_dataset(tmp_path):
    store=FeedbackStore(tmp_path/'dataset')
    sessions=[]
    for image_index in range(4):
        image=np.zeros((190,300),np.uint8)
        for j in range(5):
            y=15+j*32
            image[y:y+7,20+image_index:100+image_index*3]=200
            cv2.circle(image,(240,y+3),4,200,-1)
        baseline=analyze(image,.25,f'image-{image_index}.tiff',min_area=1000)
        r=ReviewSession(baseline,store,f'image-{image_index}.tiff')
        role='training' if image_index<2 else 'evaluation'
        for obj in r.objects:
            label='accepted' if obj['measurement']['centroid_x']<150 else 'rejected'
            store.correct(r.analysis_key,obj['fiber_id'],label,role,'rejected')
        r.refresh();sessions.append(r)
    return store,sessions


def test_real_retraining_promotes_improvement_and_predicts_new_image(reviewed_dataset):
    store,sessions=reviewed_dataset
    model_id,report=retrain(store)
    assert report['promoted']
    assert report['candidate']['balanced_accuracy'] > report['baseline']['balanced_accuracy']
    assert report['candidate']['accepted_precision'] >= report['baseline']['accepted_precision']
    assert not set(report['training_images']) & set(report['evaluation_images'])
    model=store.active_model()
    assert model['model_id'] == model_id
    _,samples=store.training_snapshot()
    training=[s for s in samples if s['role']=='training']
    assert np.allclose(model['payload']['mean'],np.mean([s['features'] for s in training],axis=0))
    # Inference on a new image with no manual overrides; segmentation is identical.
    image=sessions[0].baseline.original.copy()
    image[0,0]=1
    b=analyze(image,.25,'unseen.tiff',min_area=1000)
    r=ReviewSession(b,store,'unseen.tiff')
    assert r.result.summary['accepted_count'] == 5
    assert set(r.result.table.decision_source) == {'model'}
    assert r.result.table.manual_classification.isna().all()
    assert np.array_equal(r.result.mask,b.mask) and np.array_equal(r.result.labels,b.labels)
    assert r.result.summary['accepted_measurement_count'] == 5
    # A future model suggestion cannot override an explicit human correction.
    accepted_id=int(r.result.table.loc[r.result.table.classification.eq('accepted'),'fiber_id'].iloc[0])
    r.correct(accepted_id,'rejected','training');r.refresh()
    assert r.result.summary['accepted_count'] == 4
    assert r.result.table.loc[r.result.table.fiber_id.eq(accepted_id),'decision_source'].iloc[0] == 'manual'


def test_no_improvement_keeps_active_version_and_preserves_candidates(reviewed_dataset):
    store,_=reviewed_dataset
    approved_id,_=retrain(store)
    candidate_id,report=retrain(store)
    assert not report['promoted']
    assert store.active_model()['model_id'] == approved_id
    assert len(store.models()) == 2
    with pytest.raises(ValueError,match='approved'):
        store.activate(candidate_id)
    store.activate()
    assert store.active_model() is None
    store.activate(approved_id)
    assert store.active_model()['model_id'] == approved_id


def test_used_for_training_is_versioned_and_evaluation_is_never_fitted(reviewed_dataset):
    store,sessions=reviewed_dataset
    retrain(store)
    train=store.objects(sessions[0].analysis_key)
    heldout=store.objects(sessions[2].analysis_key)
    assert all(o['used_for_training'] and not o['used_for_evaluation'] for o in train)
    assert all(not o['used_for_training'] and o['used_for_evaluation'] for o in heldout)
    old_history=store.history(sessions[0].analysis_key)
    store.undo(sessions[0].analysis_key)
    new_history=store.history(sessions[0].analysis_key)
    assert old_history[-1]['used_for_training'] == new_history[-2]['used_for_training'] == 1
    assert new_history[-1]['action'] == 'undo' and not new_history[-1]['used_for_training']
    # Undo removes this current example, while preserving the version's exact sample.
    assert len(store.training_snapshot()[1]) == 39


def test_training_rejects_too_few_images_or_missing_class(tmp_path):
    store=FeedbackStore(tmp_path/'empty')
    with pytest.raises(ValueError,match='two training images'):
        retrain(store)
    assert store.models() == []


def test_concurrent_correction_blocks_model_promotion(reviewed_dataset,monkeypatch):
    from collagen import learning
    store,sessions=reviewed_dataset
    fit=learning.fit_classifier
    def changed_during_fit(samples):
        model=fit(samples)
        r=sessions[0]
        store.correct(r.analysis_key,1,'accepted','training','rejected')
        return model
    monkeypatch.setattr(learning,'fit_classifier',changed_during_fit)
    _,report=retrain(store)
    assert not report['promoted'] and 'changed during training' in report['reason']
    assert store.active_model() is None
    assert len(store.models()) == 1


def test_duplicate_masks_across_settings_do_not_duplicate_examples(reviewed_dataset):
    store,sessions=reviewed_dataset
    r=sessions[0]
    again=ReviewSession(analyze(r.baseline.original,.5,'renamed.png',min_area=999),store,'renamed.png')
    store.correct(again.analysis_key,1,'accepted','training','rejected')
    assert len(store.training_snapshot()[1]) == 40
    assert store.image_role(again.image_key) == 'training'
    with pytest.raises(ValueError,match='locked'):
        store.correct(again.analysis_key,2,'rejected','evaluation','rejected')


def test_model_validation_and_metrics_do_not_accept_invalid_payloads():
    with pytest.raises(ValueError,match='schema'):
        predict({'schema':100},np.zeros((2,len(FEATURE_NAMES))))
    m=classification_metrics([True,True,False,False],[True,False,True,False])
    assert m['balanced_accuracy'] == m['accepted_precision'] == .5
    assert m['false_accepted'] == m['false_rejected'] == 1
