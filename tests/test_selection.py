from dataclasses import replace
from io import BytesIO
import json
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
import pytest

from collagen.export import result_archive
from collagen.feedback import FeedbackStore
from collagen.pipeline import analyze
from collagen.review import ReviewSession
from collagen.selection import SelectionFilters, apply_selection


def table(**changes):
    row=dict(fiber_id=1,image_id='calibrated.tiff',calibration_um_per_px=.01,area_px=200,
             length_um=5.,mean_diameter_um=.2,diameter_sd_um=.01,diameter_samples=30,
             border_touch=False,crossing_merge=False,excluded_region=False,qc_flags='',
             automatic_classification='accepted',manual_classification=None,
             classification='accepted',review_status='accepted',decision_source='baseline',model_version='baseline')
    return pd.DataFrame([{**row,**changes}])


@pytest.mark.parametrize('diameter,expected',[(.149,'out_of_range'),(.15,'accepted'),(.2,'accepted'),(.335,'accepted'),(.336,'out_of_range')])
def test_diameter_boundaries_are_inclusive_in_nanometers(diameter,expected):
    filters=SelectionFilters(research_range_enabled=True)
    source=table(mean_diameter_um=diameter)
    before=source.copy(deep=True)
    result=apply_selection(source,filters)
    assert result.selection_category.iloc[0]==expected
    assert result.mean_diameter_nm.iloc[0]==pytest.approx(diameter*1000)
    pd.testing.assert_frame_equal(source,before)
    for name in ('length_um','mean_diameter_um','diameter_sd_um','area_px'):
        pd.testing.assert_series_equal(result[name],source[name])


def test_range_is_optional_and_does_not_define_all_other_fibers_as_invalid():
    assert apply_selection(table(mean_diameter_um=1.),SelectionFilters()).selection_category.iloc[0]=='accepted'


def test_calibration_uses_linear_width_and_squared_area_independently():
    filters=SelectionFilters(min_area_um2=.02,min_length_um=5.,min_width_px=20.)
    row=apply_selection(table(),filters).iloc[0]
    assert row.selection_category=='accepted'
    assert row.area_um2==pytest.approx(.02)
    assert row.mean_width_px==pytest.approx(20.)
    assert row.diameter_sd_nm==10.
    # Same physical diameter with coarser sampling no longer meets pixel reliability.
    coarse=apply_selection(table(calibration_um_per_px=.1),filters).iloc[0]
    assert coarse.selection_category=='too_small'
    assert 'below_reliable_mean_width_px' in coarse.exclusion_reason
    assert coarse.area_um2==pytest.approx(2.)  # Not a diameter-derived area estimate.


def test_tiny_physical_units_do_not_bypass_thresholds_through_absolute_tolerance():
    result=apply_selection(table(calibration_um_per_px=1e-7),SelectionFilters(min_area_um2=3e-12))
    assert result.selection_category.iloc[0]=='too_small'
    assert 'below_min_area_um2' in result.exclusion_reason.iloc[0]


@pytest.mark.parametrize('change,filters,reason',[
    ({'area_px':199},SelectionFilters(min_area_um2=.02),'below_min_area_um2'),
    ({'length_um':4.9},SelectionFilters(min_length_um=5.),'below_min_length_um'),
    ({'mean_diameter_um':.029},SelectionFilters(min_width_px=3.),'below_reliable_mean_width_px'),
])
def test_minimum_size_thresholds_keep_and_flag_detections(change,filters,reason):
    result=apply_selection(table(**change),filters)
    assert len(result)==1 and result.selection_category.iloc[0]=='too_small'
    assert reason in result.exclusion_reason.iloc[0]
    assert not result.included_in_measurement_statistics.iloc[0]


def test_manual_reject_differs_from_automatic_exclusion_and_range_can_be_overridden():
    filters=SelectionFilters(research_range_enabled=True)
    row=apply_selection(table(mean_diameter_um=.5,manual_classification='accepted'),filters).iloc[0]
    assert row.automatic_category=='out_of_range' and row.selection_category=='accepted'
    assert row.selection_override=='manual_override_out_of_range'
    assert row.included_in_measurement_statistics
    rejected=apply_selection(table(mean_diameter_um=.5,manual_classification='rejected'),filters).iloc[0]
    assert rejected.selection_category=='rejected' and rejected.exclusion_reason=='manual_rejection'
    assert rejected.automatic_category=='out_of_range'
    too_small=apply_selection(table(mean_diameter_um=.01,manual_classification='accepted'),filters).iloc[0]
    assert too_small.selection_category=='too_small' and too_small.requires_manual_review
    assert not too_small.included_in_measurement_statistics


def test_ambiguous_geometry_cannot_be_created_by_acceptance_or_policy_change():
    for policy in ('review','exclude'):
        row=apply_selection(table(crossing_merge=True,length_um=np.nan,mean_diameter_um=np.nan,
                                  manual_classification='accepted'),SelectionFilters(crossing_policy=policy)).iloc[0]
        assert row.selection_category=='ambiguous' and not row.included_in_measurement_statistics
        assert np.isnan(row.length_um) and row.requires_manual_review
    excluded=apply_selection(table(crossing_merge=True,length_um=np.nan,mean_diameter_um=np.nan),
                             SelectionFilters(crossing_policy='exclude')).iloc[0]
    assert not excluded.requires_manual_review


def test_short_simple_fiber_without_reliable_width_samples_stays_too_small():
    source=table(length_um=.01,mean_diameter_um=np.nan,diameter_sd_um=np.nan,
                 qc_flags='no_reliable_diameter',manual_classification='accepted')
    row=apply_selection(source,SelectionFilters()).iloc[0]
    assert row.selection_category=='too_small' and row.exclusion_reason=='no_reliable_diameter_samples'
    assert not row.included_in_measurement_statistics and np.isnan(row.mean_diameter_um)


def test_border_option_retains_partial_length_and_quality_flags():
    source=table(border_touch=True,qc_flags='border_truncated',automatic_classification='rejected')
    assert apply_selection(source,SelectionFilters()).selection_category.iloc[0]=='ambiguous'
    allowed=apply_selection(source,SelectionFilters(exclude_border=False)).iloc[0]
    assert allowed.selection_category=='accepted' and allowed.included_in_measurement_statistics
    assert allowed.measurement_status=='partial_or_flagged'
    assert 'partial_length_border_included_by_policy' in allowed.quality_flags
    assert allowed.length_um==5.
    barred=apply_selection(table(excluded_region=True,manual_classification='accepted'),
                           SelectionFilters(exclude_border=False)).iloc[0]
    assert barred.selection_category=='ambiguous' and 'excluded_region' in barred.exclusion_reason


def test_unvalidated_noise_suggestion_is_auditable_and_human_can_override():
    source=table(automatic_classification='rejected',decision_source='model',model_version='model-1')
    row=apply_selection(source,SelectionFilters()).iloc[0]
    assert row.selection_category=='noise' and 'unvalidated' in row.quality_flags
    source['manual_classification']='accepted'
    assert apply_selection(source,SelectionFilters()).selection_category.iloc[0]=='accepted'


@pytest.mark.parametrize('kwargs',[{'min_width_px':-1},{'min_area_um2':float('nan')},
                                 {'min_length_um':float('inf')},{'min_diameter_nm':400},
                                 {'crossing_policy':'silently_accept'}])
def test_invalid_filters_fail_explicitly(kwargs):
    with pytest.raises(ValueError):SelectionFilters(**kwargs)


def test_filter_changes_and_fast_review_do_not_measure_or_change_masks(tmp_path):
    image=np.full((100,200),80,np.uint8);image[20:29,20:150]=200
    baseline=analyze(image,.01,'fixed.tiff')
    store=FeedbackStore(tmp_path/'review')
    filters=SelectionFilters(research_range_enabled=True)
    review=ReviewSession(baseline,store,'fixed.tiff',filters=filters,render=False)
    original=review.result.table[['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']].copy()
    key=review.analysis_key
    with patch('collagen.review.measure',side_effect=AssertionError('Re-measured cached geometry')):
        review.set_filters(replace(filters,min_width_px=100))
        assert review.result.table.selection_category.tolist()==['too_small']
        review.correct(1,'accepted','training');review.refresh()
        assert review.result.summary['accepted_measurement_count']==0
        review.set_filters(filters)
        assert review.analysis_key==key and review.result.summary['accepted_measurement_count']==1
        assert review.store.undo(key);review.refresh()
        assert review.store.redo(key);review.refresh()
        pd.testing.assert_frame_equal(review.result.table[original.columns],original)
        np.testing.assert_array_equal(review.result.labels,baseline.labels)
        np.testing.assert_array_equal(review.result.original,image)
        with zipfile.ZipFile(BytesIO(result_archive(review.result))) as archive:
            exported=pd.read_csv(BytesIO(archive.read('fibers.csv')))
            assert exported.mean_diameter_nm.iloc[0]==pytest.approx(exported.mean_diameter_um.iloc[0]*1000)
            assert json.loads(exported.filter_thresholds.iloc[0])==filters.parameters()
            assert json.loads(archive.read('analysis_parameters.json'))['selection_filters']==filters.parameters()
            assert {'baseline_labels.tiff','original.tiff','count_overlay.tiff'}<=set(archive.namelist())
