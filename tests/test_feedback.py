from io import BytesIO
import json
import sqlite3
import zipfile

import numpy as np
import pandas as pd
import pytest
import tifffile

from collagen.export import result_archive, csv_bytes
from collagen.feedback import FeedbackStore, image_identity, unpack_mask
from collagen.geometry import measure
from collagen.pipeline import analyze
from collagen.review import ReviewSession, polygon_mask
from collagen.visualization import count_overlay


def session(tmp_path, image=None, **kwargs):
    if image is None:
        image = np.zeros((100,160), np.uint16)
        image[20:29,20:120] = 40000
        image[40:49,20:120] = 40000
    return ReviewSession(analyze(image, .5, 'original.tiff', **kwargs),
                         FeedbackStore(tmp_path / 'db'), 'original.tiff', 'upload-hash')


def event(review, x, y, **kwargs):
    return dict(context=review.context, token='click-1', action='toggle', x=x, y=y, **kwargs)


def test_exact_click_updates_one_object_measurements_and_exports(tmp_path):
    r = session(tmp_path)
    before = r.baseline.table.copy(deep=True)
    assert r.result.summary['accepted_count'] == 2
    r.handle_event(event(r,25,24), 'training')
    r.refresh()
    assert r.result.table.classification.tolist() == ['rejected','accepted']
    assert r.result.summary['accepted_count'] == r.result.summary['accepted_measurement_count'] == 1
    assert r.result.summary['accepted_mean_length_um'] == before.iloc[1].length_um
    pd.testing.assert_frame_equal(r.baseline.table, before)
    assert np.array_equal(r.result.mask, r.baseline.mask)
    assert np.array_equal(r.result.original, r.baseline.original)
    overlay = count_overlay(r.result.original,r.result.labels,r.result.table,opacity=1)
    assert tuple(overlay[24,25]) == (255,0,0)
    assert tuple(overlay[44,25]) == (0,255,0)
    csv = pd.read_csv(BytesIO(csv_bytes(r.result.table)))
    assert csv.classification.tolist() == ['rejected','accepted']
    assert csv.manual_classification.iloc[0] == 'rejected'
    assert csv.original_classification.iloc[0] == 'accepted'
    assert not csv.used_for_training.any()
    with zipfile.ZipFile(BytesIO(result_archive(r.result))) as z:
        assert np.array_equal(tifffile.imread(BytesIO(z.read('original.tiff'))),r.baseline.original)
        assert np.array_equal(tifffile.imread(BytesIO(z.read('baseline_labels.tiff'))),r.baseline.labels)
        assert tuple(tifffile.imread(BytesIO(z.read('count_overlay.tiff')))[24,25]) != (0,255,0)


def test_persistence_undo_calibration_and_filename_identity(tmp_path):
    r = session(tmp_path)
    r.correct(1,'rejected','evaluation')
    reopened = ReviewSession(analyze(r.baseline.original,.25,'renamed.png'),
                             FeedbackStore(tmp_path/'db'),'renamed.png','different-upload-hash')
    assert reopened.analysis_key == r.analysis_key
    assert reopened.result.table.classification.tolist() == ['rejected','accepted']
    assert reopened.result.table.length_um.iloc[1] == r.result.table.length_um.iloc[1] / 2
    assert reopened.store.image_role(reopened.image_key) == 'evaluation'
    assert reopened.store.undo(reopened.analysis_key)
    reopened.refresh()
    assert reopened.result.summary['accepted_measurement_count'] == 2
    assert not reopened.store.undo(reopened.analysis_key)
    history = reopened.store.history()
    assert [e['action'] for e in history] == ['classification','undo']
    assert history[1]['undo_target'] == history[0]['event_id']
    assert history[0]['original_classification'] == 'accepted'
    masks = reopened.store.objects(reopened.analysis_key)
    assert np.array_equal(unpack_mask(masks[0]['mask'],masks[0]['bbox']),
                          r.baseline.labels[20:29,20:120] == 1)


def test_background_stale_context_and_duplicate_events_do_not_change_neighbor(tmp_path):
    r = session(tmp_path)
    with pytest.raises(ValueError,match='No detected object'):
        r.handle_event(event(r,25,30),'training')
    first = event(r,25,24)
    r.handle_event(first,'training')
    assert r.store.correct(r.analysis_key,1,'accepted','training','accepted',token='click-1') is False
    r.refresh()
    with pytest.raises(ValueError,match='older image'):
        r.handle_event(first,'training')
    assert r.result.table.classification.tolist() == ['rejected','accepted']
    changed = r.baseline.original.copy()
    changed[0,0] = 100
    other = session(tmp_path,changed)
    assert image_identity(changed) != r.image_key
    with pytest.raises(ValueError,match='older image'):
        other.handle_event(first,'training')


@pytest.mark.parametrize('x,y',[(-.1,24),(160,24),(25,100),(float('nan'),24)])
def test_invalid_coordinates_do_not_modify_review(tmp_path,x,y):
    r = session(tmp_path)
    with pytest.raises(ValueError,match='coordinates'):
        r.handle_event(event(r,x,y),'training')
    assert not r.store.history()


def test_new_settings_do_not_reuse_old_candidate_ids(tmp_path):
    r = session(tmp_path)
    r.correct(1,'rejected','training')
    other = session(tmp_path,min_area=10000)
    assert other.analysis_key != r.analysis_key
    assert other.result.table.manual_classification.isna().all()
    assert other.result.summary['accepted_count'] == 0


def test_accept_ambiguous_and_border_objects_keeps_measurements_honest(tmp_path):
    image=np.zeros((100,160),np.uint8)
    image[20:27,20:100]=255
    image[10:60,55:62]=255
    image[75:84,:100]=255
    r=session(tmp_path,image)
    for fiber_id in (1,2):
        r.correct(fiber_id,'accepted','training'); r.refresh()
    assert r.result.summary['accepted_count'] == 2
    assert r.result.summary['accepted_measurement_count'] == 0
    assert r.result.summary['accepted_pending_measurement_count'] == 2
    assert np.isnan(r.result.summary['accepted_mean_length_um'])
    branch=r.result.table[r.result.table.crossing_merge].iloc[0]
    assert branch.classification == 'accepted'
    assert np.isnan(branch.length_um)
    assert 'crossing_or_merge' in branch.qc_flags
    assert not branch.included_in_measurement_statistics


def test_add_missed_fiber_measures_exact_outline_and_undo_restores_labels(tmp_path):
    r=session(tmp_path,np.zeros((100,160),np.uint8))
    points=[[20,30],[120,30],[120,40],[20,40]]
    fiber_id=r.add_outline(points,'training')
    r.refresh()
    assert r.result.summary['accepted_count'] == r.result.summary['accepted_measurement_count'] == 1
    mask,bbox=polygon_mask(points,r.baseline.original.shape)
    expected=measure(mask,.5)
    row=r.result.table.iloc[0]
    assert row.length_um == expected['length_um']
    assert row.mean_diameter_um == expected['mean_diameter_um']
    assert row.object_source == 'manual' and row.original_classification == 'not_detected'
    assert not r.result.mask.any()  # Original segmentation was never edited.
    with zipfile.ZipFile(BytesIO(result_archive(r.result))) as z:
        masks=np.load(BytesIO(z.read('manual_masks.npz')),allow_pickle=False)
        assert np.array_equal(masks[f'mask_{fiber_id}'],mask)
        assert np.array_equal(masks[f'bbox_yxyx_{fiber_id}'],bbox)
    assert r.store.undo(r.analysis_key)
    r.refresh()
    assert r.result.table.empty and not r.result.labels.any()
    assert r.add_outline(points,'training') > fiber_id  # IDs never recycled.


def test_outline_requires_explicit_rejection_before_overlapping_another_object(tmp_path):
    r=session(tmp_path)
    points=[[20,20],[119,20],[119,28],[20,28]]
    with pytest.raises(ValueError,match='overlaps accepted'):
        r.add_outline(points,'training')
    r.correct(1,'rejected','training');r.refresh()
    new_id=r.add_outline(points,'training');r.refresh()
    assert r.result.summary['accepted_count'] == 2
    original=r.result.table[r.result.table.fiber_id.eq(1)].iloc[0]
    assert original.classification == 'rejected' and not original.visible_in_overlay
    assert 'superseded' in original.qc_flags
    count_overlay(r.result.original,r.result.labels,r.result.table)  # Hidden original retained in CSV.
    with pytest.raises(ValueError,match='overlaps objects'):
        r.correct(1,'accepted','training')
    assert int(r.result.labels[24,25]) == new_id
    r.store.undo(r.analysis_key);r.refresh()
    assert np.array_equal(r.result.labels,r.baseline.labels)
    assert r.result.table.classification.tolist() == ['rejected','accepted']


@pytest.mark.parametrize('points', [[], [[1,1],[5,5],[9,9]],
    [[1,1],[9,9],[1,9],[9,1]], [[-1,1],[10,1],[10,10]], [[1,1],[10,1],[1,1]]])
def test_invalid_outlines_do_not_create_training_objects(tmp_path,points):
    r=session(tmp_path)
    with pytest.raises(ValueError):r.add_outline(points,'training')
    assert not r.store.history()


def test_roles_lock_and_backup_contains_masks_pixels_history(tmp_path):
    r=session(tmp_path)
    r.correct(1,'rejected','evaluation');r.refresh()
    with pytest.raises(ValueError,match='locked dataset role'):
        r.correct(2,'rejected','training')
    backup=tmp_path/'copy.sqlite3';backup.write_bytes(r.store.backup())
    with sqlite3.connect(backup) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert con.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        assert con.execute('SELECT count(*) FROM objects WHERE length(mask)>0').fetchone()[0] == 2
        assert con.execute('SELECT length(pixels) FROM images').fetchone()[0] > 0


def test_comparison_excludes_accepted_partial_geometry_from_error_statistics(tmp_path):
    from collagen.validation import comparison_report
    image=np.zeros((80,160),np.uint8)
    image[10:19,:100]=255
    image[40:49,20:120]=255
    r=session(tmp_path,image)
    r.correct(1,'accepted','training');r.refresh()
    manual=r.result.table[['image_id','fiber_id','length_um','mean_diameter_um']].copy()
    manual['length_um'] += 1
    report=comparison_report(r.result.table,manual)
    lengths=report.metrics.set_index('measurement').loc['length_um']
    assert report.counts['accepted_matched_count'] == 2
    assert lengths.paired_count == lengths.excluded_by_measurement_policy_count == 1
    assert lengths.bias_um == -1
