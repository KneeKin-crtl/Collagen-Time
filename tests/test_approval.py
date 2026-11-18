from io import BytesIO
import json
from unittest.mock import patch

import numpy as np
import pandas as pd

from collagen.editor import editor_objects
from collagen.export import csv_bytes
from collagen.feedback import FeedbackStore
from collagen.pipeline import analyze
from collagen.quality import approval_block_details
from collagen.review import ReviewSession
from collagen.selection import SelectionFilters


def review_fixture(tmp_path):
    image=np.full((320,700),80,np.uint8)
    image[30:41,30:140]=200
    image[70:81,30:140]=200
    image[200:211,300:601]=200
    for x in (330,370,410,450,490,530):
        image[150:251,x:x+5]=200
    return ReviewSession(analyze(image,.5,'original.png'),FeedbackStore(tmp_path),
                         'original.png',filters=SelectionFilters(),render=False)


def click(review,action,x,y):
    review.handle_event(dict(context=review.context,token=f'{action}-{review.revision}',
                             action=action,x=x,y=y),'training')
    review.refresh()


def test_crossing_approval_explains_missing_measurements_without_fake_inclusion(tmp_path):
    review=review_fixture(tmp_path)
    before=review.result.table.copy(deep=True)
    row=before.iloc[2]
    assert len(json.loads(row.endpoints))==14
    assert row.crossing_merge and row.diameter_samples==0
    assert np.isnan(row.length_um) and np.isnan(row.mean_diameter_um)
    with patch('collagen.review.measure',side_effect=AssertionError('Classification remeasured geometry')):
        click(review,'accept',310,205)
    row=review.result.table.iloc[2]
    details=approval_block_details(row)
    assert details['can_outline'] and '14 endpoints' in details['message']
    assert 'Length / Diameter unavailable' in details['message']
    assert 'No reliable diameter samples' in details['message']
    assert row.manual_classification=='accepted' and not row.included_in_measurement_statistics
    assert review.result.summary['accepted_measurement_count']==2
    metadata=editor_objects(review.result.table)[2]
    assert not metadata['accepted'] and not metadata['can_accept'] and metadata['can_outline']
    assert metadata['accept_block_message']==details['message']
    geometry=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    pd.testing.assert_frame_equal(review.result.table[geometry],before[geometry])
    exported=pd.read_csv(BytesIO(csv_bytes(review.result.table)))
    assert exported.iloc[2].manual_classification=='accepted'
    assert not exported.iloc[2].included_in_measurement_statistics
    assert np.isnan(exported.iloc[2].length_um) and np.isnan(exported.iloc[2].mean_diameter_um)


def test_valid_approvals_repeat_without_touching_neighbor_or_measurements(tmp_path):
    review=review_fixture(tmp_path)
    before=review.result.table.copy(deep=True)
    geometry=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    with patch('collagen.review.measure',side_effect=AssertionError('Classification remeasured geometry')):
        for action in ('accept','reject','accept','reject','accept'):
            click(review,action,50,35)
            assert review.result.table.iloc[0].included_in_measurement_statistics==(action=='accept')
            assert pd.isna(review.result.table.iloc[1].manual_classification)
            pd.testing.assert_frame_equal(review.result.table[geometry],before[geometry])
        click(review,'accept',50,75)
    assert review.result.table.manual_classification.iloc[:2].tolist()==['accepted','accepted']
    assert pd.isna(review.result.table.iloc[2].manual_classification)
    np.testing.assert_array_equal(review.result.labels,review.baseline.labels)
    np.testing.assert_array_equal(review.result.original,review.baseline.original)
    assert approval_block_details(review.result.table.iloc[0])==dict(message='',can_outline=False)


def test_outline_resolves_crossing_as_a_new_measured_fiber_preserving_original(tmp_path):
    review=review_fixture(tmp_path)
    click(review,'accept',310,205)
    original=review.result.table.iloc[2].copy()
    baseline_labels=review.baseline.labels.copy()
    new_id=review.add_outline([[300,200],[600,200],[600,210],[300,210]],'training')
    review.refresh()
    assert new_id==4 and review.result.summary['accepted_measurement_count']==3
    old,new=review.result.table.iloc[2],review.result.table.iloc[3]
    assert old.fiber_id==original.fiber_id and old.manual_classification=='accepted'
    assert not old.included_in_measurement_statistics and np.isnan(old.length_um)
    assert new.included_in_measurement_statistics and new.length_um>0 and new.mean_diameter_um>0
    assert new.object_source=='manual'
    assert [x['fiber_id'] for x in review.result.id_labels]==[1,2,4]
    np.testing.assert_array_equal(review.baseline.labels,baseline_labels)
    for action in ('reject','accept'):
        click(review,action,310,205)
        assert review.result.table.iloc[3].included_in_measurement_statistics==(action=='accept')
    reopened=ReviewSession(review.baseline,FeedbackStore(tmp_path),'original.png',
                           filters=SelectionFilters(),render=False)
    assert reopened.result.table.fiber_id.tolist()==[1,2,3,4]
    assert reopened.result.table.iloc[3].included_in_measurement_statistics


def test_excluded_region_does_not_offer_an_outline_as_a_filter_bypass():
    row=dict(automatic_category='ambiguous',automatic_exclusion_reason='excluded_region;ambiguous_or_unavailable_geometry',
             qc_flags='crossing_or_merge;no_reliable_diameter',crossing_merge=True,endpoints='invalid json',
             length_um=np.nan,mean_diameter_um=np.nan,diameter_samples=0)
    details=approval_block_details(row)
    assert not details['can_outline'] and 'explicitly excluded image region' in details['message']
    assert '0 endpoints' not in details['message'], 'Malformed metadata is not a zero-endpoint measurement.'
