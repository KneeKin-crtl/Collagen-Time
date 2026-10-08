from io import BytesIO
import json
import zipfile
import cv2
import numpy as np
import pandas as pd
import pytest
import tifffile
from collagen.loading import load_image
from collagen.pipeline import analyze
from collagen.export import result_archive
from collagen.segmentation import ThresholdSegmenter
from collagen.validation import compare_ground_truth


def test_segmentation_and_qc_export():
    image = np.zeros((120, 160), np.uint16)
    image[15:22, 20:100] = 40000
    image[40:47, :70] = 40000
    image[65:70, 60:120] = 40000
    image[50:100, 85:90] = 40000
    image[110, 150] = 40000
    result = analyze(image, .5, 'synthetic')
    assert result.mask.shape == image.shape
    assert np.array_equal(result.mask, image > 0)
    assert result.summary['accepted_isolated_count'] == 1
    assert result.summary['candidate_count'] == 4
    assert result.summary['review_required_count'] == 3
    assert result.table.fiber_id.is_unique
    assert result.table.border_touch.sum() == 1
    assert result.table.crossing_merge.sum() == 1
    assert 'below_min_area' in ';'.join(result.table.qc_flags)
    with zipfile.ZipFile(BytesIO(result_archive(result))) as archive:
        assert len(archive.namelist()) == 6
        assert np.array_equal(tifffile.imread(BytesIO(archive.read('original.tiff'))), image)
        labels = tifffile.imread(BytesIO(archive.read('labels.tiff')))
        assert np.array_equal(labels, result.labels)
        assert len(pd.read_csv(BytesIO(archive.read('fibers.csv')))) == 4
        assert json.loads(archive.read('summary.json'))['candidate_count'] == 4


def test_empty_and_dark_segmentation():
    result = analyze(np.zeros((20, 30), np.uint8), 1)
    assert result.table.empty
    assert result.summary['candidate_count'] == 0
    image = np.ones((30, 60))
    image[10:15, 10:50] = 0
    result = analyze(image, 1, segmenter=ThresholdSegmenter(bright=False))
    assert result.summary['accepted_isolated_count'] == 1


def test_custom_segmenter_and_loop_review():
    mask = np.zeros((80, 80), bool)
    from skimage.draw import disk
    rr, cc = disk((40, 40), 25, shape=mask.shape)
    mask[rr, cc] = True
    rr, cc = disk((40, 40), 20, shape=mask.shape)
    mask[rr, cc] = False
    result = analyze(np.zeros(mask.shape), 1, segmenter=lambda image: mask)
    assert result.summary['accepted_isolated_count'] == 0
    assert 'non_simple_centerline' in result.table.iloc[0].qc_flags


@pytest.mark.parametrize('extension', ['png', 'jpg', 'tif'])
def test_image_loading(extension):
    image = np.full((20, 30, 3), 120, np.uint8)
    if extension == 'tif':
        buffer = BytesIO()
        tifffile.imwrite(buffer, image)
        data = buffer.getvalue()
    else:
        ok, encoded = cv2.imencode('.' + extension, image)
        assert ok
        data = encoded.tobytes()
    assert load_image(data, 'sample.' + extension).shape == image.shape


def test_tiff_stack_rejected():
    buffer = BytesIO()
    tifffile.imwrite(buffer, np.zeros((3, 10, 10), np.uint16), photometric='minisblack')
    with pytest.raises(ValueError, match='single-plane'):
        load_image(buffer.getvalue(), 'stack.tif')


def test_ground_truth_matching_and_unmatched():
    auto = pd.DataFrame({'image_id': ['x', 'x'], 'fiber_id': [1, 2], 'length_um': [12., 8.], 'mean_diameter_um': [3., 2.], 'review_status': ['accepted', 'accepted']})
    manual = pd.DataFrame({'image_id': ['x', 'x'], 'fiber_id': [1, 3], 'length_um': [10., 9.], 'mean_diameter_um': [2., 2.]})
    joined = compare_ground_truth(auto, manual)
    assert joined.loc[joined.fiber_id == 1, 'length_um_error'].iloc[0] == 2
    assert set(joined['_merge'].astype(str)) == {'both', 'left_only', 'right_only'}
    with pytest.raises(ValueError, match='unique'):
        compare_ground_truth(auto, pd.concat([manual, manual]))
