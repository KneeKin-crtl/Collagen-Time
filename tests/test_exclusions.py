from io import BytesIO
import json
import zipfile
import numpy as np
import pytest
from collagen.pipeline import analyze, analyze_pixels, calibrate_analysis
from collagen.exclusions import suggest_scale_bar_region
from collagen.export import result_archive
from collagen.loading import load_image
from collagen.validation import comparison_report


def panel_image():
    image = np.full((240, 400), 128, np.uint8)
    image[100:105, 50:130] = 0
    image[200:230, 330:395] = 255
    image[208:212, 340:385] = 0
    return image


def test_confirmed_region_flags_whole_objects_without_cutting_mask():
    from collagen.segmentation import ThresholdSegmenter
    image = panel_image()
    proposal = suggest_scale_bar_region(image)
    assert proposal is not None
    before = analyze(image, .2, segmenter=ThresholdSegmenter(bright=False))
    result = analyze(image, .2, segmenter=ThresholdSegmenter(bright=False), exclusion_regions=[proposal])
    assert before.summary['accepted_isolated_count'] == 2
    assert result.summary['accepted_isolated_count'] == 1
    assert result.summary['excluded_region_count'] == 1
    assert np.array_equal(before.mask, result.mask)
    assert np.array_equal(before.labels, result.labels)
    assert np.array_equal(result.original, image)
    assert len(result.table) == 2
    excluded = result.table[result.table.excluded_region].iloc[0]
    assert excluded.review_status == 'review_required'
    assert 'excluded_region' in excluded.qc_flags
    assert excluded.exclusion_overlap_px > 0
    with zipfile.ZipFile(BytesIO(result_archive(result))) as archive:
        params = json.loads(archive.read('analysis_parameters.json'))
        assert params['exclusion_regions_xyxy'] == [list(proposal)]
        assert params['calibration_um_per_px'] == .2


def test_partial_intersection_does_not_fabricate_new_endpoints():
    image = np.zeros((60, 120), np.uint8)
    image[25:32, 10:110] = 255
    from collagen.segmentation import ThresholdSegmenter
    before = analyze(image, .3, segmenter=ThresholdSegmenter(bright=True))
    result = analyze(image, .3, segmenter=ThresholdSegmenter(bright=True), exclusion_regions=[(60, 20, 80, 40)])
    assert result.summary['accepted_isolated_count'] == 0
    assert len(result.table) == 1
    assert result.table.iloc[0].endpoints == before.table.iloc[0].endpoints
    assert result.table.iloc[0].length_um == before.table.iloc[0].length_um


def test_excluded_scale_candidate_cannot_enter_accuracy_statistics():
    from collagen.segmentation import ThresholdSegmenter
    image = panel_image()
    before = analyze(image, .2, segmenter=ThresholdSegmenter(bright=False))
    result = analyze(image, .2, segmenter=ThresholdSegmenter(bright=False),
                     exclusion_regions=[suggest_scale_bar_region(image)])
    manual = before.table[['image_id', 'fiber_id', 'length_um', 'mean_diameter_um']]
    report = comparison_report(result.table, manual)
    assert report.counts['matched_count'] == 2
    assert report.counts['accepted_matched_count'] == 1
    assert report.metrics.paired_count.eq(1).all()
    excluded_id = result.table.loc[result.table.excluded_region, 'fiber_id'].iloc[0]
    assert report.rows.set_index('fiber_id').loc[excluded_id, 'length_um_included'] == False


@pytest.mark.parametrize('region', [(-1, 0, 20, 20), (10, 10, 10, 20), (0, 0, 401, 20), (0., 0, 20, 20), (0, 0, 20)])
def test_bad_exclusion_bounds_rejected(region):
    with pytest.raises(ValueError, match='Exclusion'):
        analyze(panel_image(), 1., exclusion_regions=[region])


def test_calibration_rescales_distances_without_mutating_pixel_measurements():
    image = np.zeros((80, 120), np.uint8)
    image[30:39, 15:100] = 200
    pixels = analyze_pixels(image)
    original = pixels.table.copy(deep=True)
    first = calibrate_analysis(pixels, .5)
    second = calibrate_analysis(pixels, .25)
    assert second.table.length_um.iloc[0] == pytest.approx(first.table.length_um.iloc[0] / 2)
    assert second.table.mean_diameter_um.iloc[0] == pytest.approx(first.table.mean_diameter_um.iloc[0] / 2)
    assert second.table.diameter_sd_um.iloc[0] == pytest.approx(first.table.diameter_sd_um.iloc[0] / 2)
    assert second.table.endpoints.iloc[0] == first.table.endpoints.iloc[0]
    assert pixels.table.equals(original)
    assert second.mask is pixels.mask


@pytest.mark.parametrize('calibration', [None, 0, -1, float('nan'), float('inf')])
def test_calibration_must_be_explicit_positive_and_finite(calibration):
    with pytest.raises(ValueError, match='calibration'):
        analyze(panel_image(), calibration)


@pytest.mark.parametrize('extension', ['png', 'jpg', 'jpeg', 'tiff'])
@pytest.mark.parametrize('data', [b'', b'broken image data'])
def test_bad_decodes_are_validation_errors(extension, data):
    with pytest.raises(ValueError):
        load_image(data, 'bad.' + extension)
