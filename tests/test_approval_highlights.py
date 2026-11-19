from dataclasses import replace
from io import BytesIO
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
import tifffile

from collagen.editor import editor_objects
from collagen.export import result_archive, csv_bytes
from collagen.feedback import FeedbackStore
from collagen.quality import review_approved_candidates
from collagen.review import ReviewSession
from collagen.selection import SelectionFilters
from collagen.visualization import review_overlay, count_overlay
from test_approval import review_fixture, click


def test_approval_colors_the_exact_mask_without_fabricating_measurements(tmp_path):
    r=review_fixture(tmp_path)
    original=r.result.original.copy();labels=r.result.labels.copy()
    fields=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    measurements=r.result.table[fields].copy()
    before=count_overlay(original,labels,r.result.table)
    with patch('collagen.review.measure',side_effect=AssertionError('Approval remeasured geometry')):
        click(r,'accept',310,205)
    row=r.result.table.iloc[2]
    assert row.review_approved and row.manual_classification=='accepted'
    assert not row.included_in_measurement_statistics
    assert r.result.summary['review_approved_count']==3
    assert r.result.summary['accepted_measurement_count']==2
    obj=editor_objects(r.result.table)[2]
    assert obj['approved'] and not obj['accepted'] and not obj['measurable']
    overlay=review_overlay(original,labels,r.result.table)
    assert tuple(overlay[205,310])==(80,233,80)  # #00FF00 at 60% over source RGB 200.
    assert tuple(review_overlay(original,labels,r.result.table,opacity=1)[205,310])==(0,255,0)
    # Pixel inside the object's bounding box but outside its segmented shape.
    assert labels[170,350]==0 and np.all(overlay[170,350]==original[170,350])
    assert np.array_equal(overlay[labels!=3],before[labels!=3])
    assert np.array_equal(count_overlay(original,labels,r.result.table),before)
    pd.testing.assert_frame_equal(r.result.table[fields],measurements)
    np.testing.assert_array_equal(r.result.original,original)
    np.testing.assert_array_equal(r.result.labels,labels)
    exported=pd.read_csv(BytesIO(csv_bytes(r.result.table)))
    assert exported.iloc[2].review_approved and not exported.iloc[2].included_in_measurement_statistics
    assert np.isnan(exported.iloc[2].length_um) and np.isnan(exported.iloc[2].mean_diameter_um)
    with zipfile.ZipFile(BytesIO(result_archive(r.result))) as archive:
        reviewed=tifffile.imread(BytesIO(archive.read('review_overlay.tiff')))
        counted=tifffile.imread(BytesIO(archive.read('count_overlay.tiff')))
        assert reviewed[205,310,1]>reviewed[205,310,0]
        assert counted[205,310,0]>counted[205,310,1]


def test_unmeasurable_toggle_persists_and_undo_redo_restore_approval(tmp_path):
    r=review_fixture(tmp_path)
    for expected in (True,False,True):
        click(r,'toggle',310,205)
        assert bool(r.result.table.iloc[2].review_approved)==expected
        assert not r.result.table.iloc[2].included_in_measurement_statistics
    reopened=ReviewSession(r.baseline,FeedbackStore(tmp_path),'original.png',filters=SelectionFilters(),render=False)
    assert reopened.result.table.iloc[2].review_approved
    reopened.store.undo(reopened.analysis_key);reopened.refresh()
    assert not reopened.result.table.iloc[2].review_approved
    reopened.store.redo(reopened.analysis_key);reopened.refresh()
    assert reopened.result.table.iloc[2].review_approved
    assert reopened.result.summary['accepted_measurement_count']==2


def test_filter_changes_cannot_erase_a_saved_approval_highlight(tmp_path):
    r=review_fixture(tmp_path)
    fields=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    before=r.result.table[fields].copy()
    click(r,'accept',50,35)
    with patch('collagen.review.measure',side_effect=AssertionError('Filter change remeasured geometry')):
        r.set_filters(replace(SelectionFilters(),min_width_px=100))
    assert r.result.table.review_approved.tolist()==[True,False,False]
    assert r.result.summary['accepted_measurement_count']==0
    assert editor_objects(r.result.table)[0]['approved']
    pd.testing.assert_frame_equal(r.result.table[fields],before)


def test_superseded_and_hidden_masks_do_not_reuse_historical_green_labels(tmp_path):
    r=review_fixture(tmp_path)
    click(r,'accept',310,205)
    r.add_outline([[300,200],[600,200],[600,210],[300,210]],'training');r.refresh()
    assert r.result.table.iloc[2].manual_classification=='accepted'
    assert not r.result.table.iloc[2].review_approved
    assert r.result.table.iloc[3].review_approved
    hidden=r.result.table.copy();hidden.loc[hidden.fiber_id.eq(4),'visible_in_overlay']=False
    assert not review_approved_candidates(hidden).iloc[3]


def test_review_colors_use_decisions_and_handle_missing_labels_and_empty_tables():
    table=pd.DataFrame(dict(review_status=['accepted','review_required','accepted','review_required'],
                            manual_classification=[None,'accepted','rejected',pd.NA],qc_flags=['']*4))
    table['review_approved']=True  # A stale derived column must not replace decisions.
    assert review_approved_candidates(table).tolist()==[True,True,False,False]
    assert review_approved_candidates(table.drop(columns='manual_classification')).tolist()==[True,False,True,False]
    assert review_approved_candidates(table.iloc[:0]).empty
