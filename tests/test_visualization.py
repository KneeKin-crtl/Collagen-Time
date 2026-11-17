from io import BytesIO
import zipfile
import numpy as np
import pandas as pd
import pytest
import tifffile
from collagen.export import result_archive
from collagen.pipeline import analyze
from collagen.quality import counted_candidates
from collagen.visualization import count_overlay, COUNTED_COLOR, EXCLUDED_COLOR


def decisions(statuses=('accepted', 'review_required')):
    return pd.DataFrame({'fiber_id': [8, 2], 'review_status': list(statuses)})


def test_identical_label_mask_changes_color_only_when_api_decisions_change():
    image = np.full((10, 12, 3), [30, 60, 90], np.uint8)
    labels = np.zeros((10, 12), np.int32)
    labels[2:4, 2:5] = 8
    labels[6:8, 7:10] = 2
    table = decisions()
    before_image, before_labels, before_table = image.copy(), labels.copy(), table.copy(deep=True)
    overlay = count_overlay(image, labels, table, opacity=1)
    assert np.all(overlay[labels == 8] == COUNTED_COLOR)
    assert np.all(overlay[labels == 2] == EXCLUDED_COLOR)
    assert np.array_equal(overlay[labels == 0], image[labels == 0])
    swapped = count_overlay(image, labels, decisions(('review_required', 'accepted')), opacity=1)
    assert np.all(swapped[labels == 8] == EXCLUDED_COLOR)
    assert np.all(swapped[labels == 2] == COUNTED_COLOR)
    assert np.array_equal(image, before_image)
    assert np.array_equal(labels, before_labels)
    pd.testing.assert_frame_equal(table, before_table)


def test_tint_preserves_original_rgb_and_does_not_color_background():
    image = np.full((10, 12, 3), [40, 80, 120], np.uint8)
    labels = np.zeros((10, 12), np.int32)
    labels[2:4, 2:5] = 8
    labels[6:8, 7:10] = 2
    overlay = count_overlay(image, labels, decisions(), opacity=.5)
    assert tuple(overlay[2, 2]) == (20, 168, 60)
    assert tuple(overlay[6, 7]) == (148, 40, 60)
    assert tuple(overlay[0, 0]) == (40, 80, 120)
    assert overlay.dtype == np.uint8
    assert overlay.shape == image.shape


def test_default_tint_color_follows_decision_even_on_opposite_source_colors():
    image = np.zeros((10, 12, 3), np.uint8)
    labels = np.zeros((10, 12), np.int32)
    labels[2:4, 2:5] = 8
    labels[6:8, 7:10] = 2
    image[labels == 8] = (255, 0, 0)
    image[labels == 2] = (0, 255, 0)
    overlay = count_overlay(image, labels, decisions())
    assert np.all(overlay[labels == 8, 1] > overlay[labels == 8, 0])
    assert np.all(overlay[labels == 2, 0] > overlay[labels == 2, 1])


def test_green_labels_match_final_count_and_all_qc_exclusions_are_red():
    image = np.full((180, 220, 3), [80, 90, 100], np.uint8)
    mask = np.zeros(image.shape[:2], bool)
    mask[20:27, 20:100] = True  # accepted
    mask[50:57, :60] = True  # border
    mask[80:85, 80:140] = True
    mask[60:110, 105:110] = True  # crossing/merge
    mask[140:147, 20:100] = True  # region-excluded
    mask[160, 200] = True  # below minimum area
    result = analyze(image, .2, segmenter=lambda gray: mask, exclusion_regions=[(15, 135, 105, 150)])
    overlay = count_overlay(result.original, result.labels, result.table, opacity=1)
    green_ids, red_ids = [], []
    for row in result.table.itertuples():
        pixels = overlay[result.labels == row.fiber_id]
        if row.review_status == 'accepted':
            assert np.all(pixels == COUNTED_COLOR)
            green_ids.append(row.fiber_id)
        else:
            assert np.all(pixels == EXCLUDED_COLOR)
            red_ids.append(row.fiber_id)
    assert len(green_ids) == result.summary['accepted_isolated_count'] == 1
    assert len(red_ids) == result.summary['review_required_count'] == 4
    assert set(green_ids) == set(result.table.loc[counted_candidates(result.table), 'fiber_id'])
    assert set(result.table.loc[result.table.excluded_region, 'fiber_id']) <= set(red_ids)
    with zipfile.ZipFile(BytesIO(result_archive(result))) as z:
        exported = tifffile.imread(BytesIO(z.read('count_overlay.tiff')))
        assert np.array_equal(exported, count_overlay(result.original, result.labels, result.table))


def test_empty_and_all_excluded_analyses():
    image = np.full((10, 12, 3), [40, 80, 120], np.uint8)
    labels = np.zeros((10, 12), np.int32)
    empty = pd.DataFrame(columns=['fiber_id', 'review_status'])
    assert np.array_equal(count_overlay(image, labels, empty), image)
    labels[2:4, 2:5] = 8
    labels[6:8, 7:10] = 2
    overlay = count_overlay(image, labels, decisions(('review_required', 'review_required')), opacity=1)
    assert np.all(overlay[labels > 0] == EXCLUDED_COLOR)
    assert np.array_equal(overlay[labels == 0], image[labels == 0])


@pytest.mark.parametrize('table', [
    pd.DataFrame({'fiber_id': [1], 'review_status': ['accepted']}),
    pd.DataFrame({'fiber_id': [8, 8], 'review_status': ['accepted', 'review_required']}),
    pd.DataFrame({'fiber_id': [8, 2], 'review_status': ['accepted', None]}),
    pd.DataFrame({'fiber_id': [8, 2], 'review_status': ['accepted', 'unknown']}),
    pd.DataFrame({'fiber_id': [8, 2]}),
])
def test_missing_or_inconsistent_api_decisions_fail_instead_of_guessing(table):
    image = np.zeros((10, 12), np.uint8)
    labels = np.zeros(image.shape, np.int32)
    labels[2:4, 2:5] = 8
    labels[6:8, 7:10] = 2
    with pytest.raises(ValueError):
        count_overlay(image, labels, table)
