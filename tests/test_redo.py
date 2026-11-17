import numpy as np
import pytest
import json
import sqlite3

from collagen.feedback import FeedbackStore
from collagen.pipeline import analyze
from collagen.review import ReviewSession
from collagen.selection import SelectionFilters


def session(tmp_path):
    image=np.zeros((100,180),np.uint8);image[20:29,20:150]=200
    return ReviewSession(analyze(image,.1,'history.png'),FeedbackStore(tmp_path/'data'),'history.png')


def test_undo_redo_survives_reopen_and_repeated_cycles_without_renumbering(tmp_path):
    review=session(tmp_path)
    before=review.result.table.copy(deep=True)
    assert not review.store.redo(review.analysis_key)
    review.correct(1,'rejected','training');review.refresh()
    for _ in range(3):
        assert review.store.undo(review.analysis_key);review.refresh()
        assert review.result.table.classification.tolist()==['accepted']
        reopened=session(tmp_path)
        assert reopened.store.redo(reopened.analysis_key);reopened.refresh()
        assert reopened.result.table.classification.tolist()==['rejected']
        review=reopened
    for name in ('fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints'):
        assert review.result.table[name].equals(before[name])
    assert [e['action'] for e in review.store.history()]==['classification','undo','redo','undo','redo','undo','redo']
    assert len(review.store.training_snapshot()[1])==1


def test_new_correction_clears_redo_without_deleting_history(tmp_path):
    review=session(tmp_path)
    review.correct(1,'rejected','training');review.refresh()
    assert review.store.undo(review.analysis_key);review.refresh()
    review.correct(1,'accepted','training');review.refresh()
    assert not review.store.redo(review.analysis_key)
    assert len(review.store.history())==3
    assert review.store.undo(review.analysis_key);review.refresh()
    assert review.store.undo(review.analysis_key) is False  # Old undone branch is not resurrected.


def test_redo_outline_restores_same_mask_id_and_measurements(tmp_path):
    review=session(tmp_path)
    fiber_id=review.add_outline([[20,60],[120,60],[120,70],[20,70]],'training');review.refresh()
    expected=review.result.table.set_index('fiber_id').loc[fiber_id].copy()
    labels=review.result.labels.copy()
    assert review.store.undo(review.analysis_key);review.refresh()
    assert fiber_id not in review.result.table.fiber_id.tolist()
    assert review.store.redo(review.analysis_key);review.refresh()
    np.testing.assert_array_equal(review.result.labels,labels)
    actual=review.result.table.set_index('fiber_id').loc[fiber_id]
    for name in ('length_um','mean_diameter_um','diameter_sd_um','endpoints'):
        assert actual[name]==expected[name]


def test_stale_and_duplicate_history_events_are_safe(tmp_path):
    review=session(tmp_path)
    review.correct(1,'rejected','training');review.refresh()
    revision=review.revision
    assert review.store.undo(review.analysis_key,revision,'undo-token')
    assert not review.store.undo(review.analysis_key,revision,'undo-token')
    with pytest.raises(ValueError,match='review changed'):
        review.store.redo(review.analysis_key,revision,'redo-token')
    review.refresh()
    assert review.store.redo(review.analysis_key,review.revision,'redo-token')
    assert not review.store.redo(review.analysis_key,revision,'redo-token')


def test_filter_context_is_saved_with_corrections_and_preserved_on_undo_redo(tmp_path):
    review=session(tmp_path)
    review.set_filters(SelectionFilters(research_range_enabled=True,min_width_px=0))
    review.correct(1,'accepted','training');review.refresh()
    context=json.loads(review.store.history()[-1]['selection_context'])
    assert context['automatic_category']=='out_of_range'
    assert context['filters']['max_diameter_nm']==335.
    assert context['calibration_um_per_px']==.1
    assert review.store.undo(review.analysis_key);review.refresh()
    assert review.store.redo(review.analysis_key);review.refresh()
    assert json.loads(review.store.history()[-1]['selection_context'])==context


def test_legacy_database_migration_keeps_events_and_enables_redo(tmp_path):
    review=session(tmp_path)
    review.correct(1,'rejected','training')
    with sqlite3.connect(review.store.path) as con:
        con.execute('ALTER TABLE events DROP COLUMN selection_context')
    reopened=session(tmp_path)
    assert len(reopened.store.history())==1
    assert reopened.store.undo(reopened.analysis_key);reopened.refresh()
    assert reopened.store.redo(reopened.analysis_key);reopened.refresh()
    assert reopened.result.table.classification.tolist()==['rejected']
