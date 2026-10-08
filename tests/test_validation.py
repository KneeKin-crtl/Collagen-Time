from io import BytesIO

import numpy as np
import pandas as pd
import pytest

from collagen.export import csv_bytes
from collagen.validation import comparison_report


def tables():
    measured = pd.DataFrame({
        'image_id': ['image'] * 5,
        'fiber_id': [1, 2, 3, 4, 5],
        'length_um': [12., 8., 100., np.nan, 9.],
        'mean_diameter_um': [3., np.nan, 50., 6., 2.],
        'review_status': ['accepted', 'accepted', 'review_required', 'accepted', 'accepted'],
    })
    manual = pd.DataFrame({
        'image_id': ['image'] * 5,
        'fiber_id': [1, 2, 3, 4, 6],
        'length_um': [10., 10., 10., 7., 6.],
        'mean_diameter_um': [2., 4., 2., 8., 1.],
    })
    return measured, manual


def test_counts_and_accepted_only_bias_mae():
    measured, manual = tables()
    report = comparison_report(measured, manual)
    assert report.counts == {
        'measured_count': 5, 'manual_count': 5, 'matched_count': 4,
        'unmatched_measured_count': 1, 'unmatched_manual_count': 1,
        'accepted_matched_count': 3, 'review_required_matched_count': 1,
    }
    metrics = report.metrics.set_index('measurement')
    length = metrics.loc['length_um']
    assert length.paired_count == 2
    assert length.missing_pair_count == 1
    assert length.bias_um == 0  # +2 and -2 cancel, absolute errors do not.
    assert length.mean_absolute_error_um == 2
    diameter = metrics.loc['mean_diameter_um']
    assert diameter.paired_count == 2
    assert diameter.missing_pair_count == 1
    assert diameter.bias_um == -.5  # +1 and -2, independent of length availability.
    assert diameter.mean_absolute_error_um == 1.5
    rows = report.rows.set_index('fiber_id')
    assert rows.loc[3, 'review_status'] == 'review_required'
    assert pd.isna(rows.loc[3, 'length_um_error'])
    assert pd.isna(rows.loc[3, 'mean_diameter_um_error'])
    assert rows.loc[5, '_merge'] == 'left_only'
    assert rows.loc[6, '_merge'] == 'right_only'
    for fiber in (3, 5, 6):
        assert not rows.loc[fiber, 'accepted_matched']
        assert not rows.loc[fiber, 'length_um_included']
        assert not rows.loc[fiber, 'mean_diameter_um_included']
    assert rows.loc[1, 'length_um_relative_error_pct'] == 20


@pytest.mark.parametrize('missing_side', ['auto', 'manual', 'both'])
def test_missing_pairs_are_not_zero_errors(missing_side):
    measured, manual = tables()
    if missing_side in ('auto', 'both'):
        measured.loc[:, 'length_um'] = np.nan
    if missing_side in ('manual', 'both'):
        manual.loc[:, 'length_um'] = np.nan
    report = comparison_report(measured, manual)
    length = report.metrics.set_index('measurement').loc['length_um']
    assert length.paired_count == 0
    assert length.missing_pair_count == 3
    assert pd.isna(length.bias_um)
    assert pd.isna(length.mean_absolute_error_um)
    assert report.rows.length_um_error.isna().all()
    assert report.rows.length_um_relative_error_pct.isna().all()
    assert report.metrics.set_index('measurement').loc['mean_diameter_um', 'paired_count'] == 2
    exported = pd.read_csv(BytesIO(csv_bytes(report.metrics)))
    assert pd.isna(exported.iloc[0].bias_um)


def test_disjoint_ids_have_no_error_pairs():
    measured, manual = tables()
    manual['fiber_id'] += 100
    report = comparison_report(measured, manual)
    assert report.counts['matched_count'] == 0
    assert report.counts['unmatched_measured_count'] == 5
    assert report.counts['unmatched_manual_count'] == 5
    assert len(report.rows) == 10
    assert report.metrics.paired_count.eq(0).all()
    assert report.metrics.missing_pair_count.eq(0).all()
    assert report.metrics.bias_um.isna().all()
    assert report.metrics.mean_absolute_error_um.isna().all()


@pytest.mark.parametrize('empty_side', ['auto', 'manual', 'both'])
def test_empty_tables(empty_side):
    measured, manual = tables()
    if empty_side in ('auto', 'both'):
        measured = measured.iloc[:0]
    if empty_side in ('manual', 'both'):
        manual = manual.iloc[:0]
    report = comparison_report(measured, manual)
    assert report.counts['matched_count'] == 0
    assert report.counts['measured_count'] == len(measured)
    assert report.counts['manual_count'] == len(manual)
    assert report.metrics.paired_count.eq(0).all()
    assert report.metrics.bias_um.isna().all()


def test_image_id_is_part_of_match_key():
    measured, manual = tables()
    manual['image_id'] = 'different_image'
    report = comparison_report(measured, manual)
    assert report.counts['matched_count'] == 0
    assert len(report.rows) == 10


def test_all_review_required_matches_have_no_accuracy_statistics():
    measured, manual = tables()
    measured['review_status'] = 'review_required'
    report = comparison_report(measured, manual)
    assert report.counts['matched_count'] == 4
    assert report.counts['review_required_matched_count'] == 4
    assert report.counts['accepted_matched_count'] == 0
    assert report.metrics.paired_count.eq(0).all()
    assert report.metrics.bias_um.isna().all()
    assert report.metrics.mean_absolute_error_um.isna().all()


def test_numeric_strings_are_normalized_without_mutating_inputs():
    measured, manual = tables()
    measured['length_um'] = measured.length_um.map(lambda x: str(x) if pd.notna(x) else None)
    manual['length_um'] = manual.length_um.astype(str)
    old_measured, old_manual = measured.copy(deep=True), manual.copy(deep=True)
    report = comparison_report(measured, manual)
    assert report.metrics.iloc[0].mean_absolute_error_um == 2
    pd.testing.assert_frame_equal(measured, old_measured)
    pd.testing.assert_frame_equal(manual, old_manual)


@pytest.mark.parametrize('status', [None, 'unknown'])
def test_invalid_status_never_defaults_to_accepted(status):
    measured, manual = tables()
    measured.loc[0, 'review_status'] = status
    with pytest.raises(ValueError, match='review_status'):
        comparison_report(measured, manual)


def test_status_is_required_for_accepted_only_reporting():
    measured, manual = tables()
    with pytest.raises(ValueError, match='review_status'):
        comparison_report(measured.drop(columns='review_status'), manual)


@pytest.mark.parametrize('bad_value', [0, -1, np.inf, 'not a measurement'])
def test_invalid_measurements_are_rejected(bad_value):
    measured, manual = tables()
    manual['length_um'] = manual.length_um.astype(object)
    manual.loc[0, 'length_um'] = bad_value
    with pytest.raises(ValueError):
        comparison_report(measured, manual)
