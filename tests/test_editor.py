import base64

import numpy as np
import pytest

from collagen.editor import original_preview
from collagen.loading import load_image


def decode_preview(url):
    return load_image(base64.b64decode(url.split(',', 1)[1]), 'original.png')


@pytest.mark.parametrize('dtype', ['uint8', 'uint16', '>u2'])
@pytest.mark.parametrize('channels', [None, 3, 4])
def test_original_preview_preserves_source_pixels_channels_and_bit_depth(dtype, channels):
    shape = (13, 21) if channels is None else (13, 21, channels)
    image = np.arange(np.prod(shape)).reshape(shape).astype(dtype)
    if image.dtype.itemsize == 2:
        image *= 41
    before = image.copy()
    url, note = original_preview(image)
    restored = decode_preview(url)
    np.testing.assert_array_equal(restored, image)
    np.testing.assert_array_equal(image, before)
    assert restored.dtype.itemsize == image.dtype.itemsize
    assert note == ''
    # PNG's IHDR bit depth must preserve 16-bit originals, not normalize them.
    assert base64.b64decode(url.split(',', 1)[1])[24] == image.dtype.itemsize * 8


def test_float_tiff_preview_explicitly_reports_browser_conversion_without_mutation():
    image = np.array([[1.25, 2.5], [3.75, 5.0]], dtype=np.float32)
    before = image.copy()
    url, note = original_preview(image)
    assert 'Browser display conversion' in note
    assert decode_preview(url).shape[:2] == image.shape
    np.testing.assert_array_equal(image, before)
