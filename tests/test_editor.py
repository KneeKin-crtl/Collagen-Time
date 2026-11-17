import base64

import numpy as np
import pytest
from types import SimpleNamespace
import pandas as pd

from collagen.editor import original_preview, editor_assets, label_sprite
from collagen.loading import load_image
from collagen.id_labels import fiber_id_layout


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


def test_identity_layer_round_trips_ids_above_single_byte_without_touching_source():
    labels=np.zeros((20,30),np.int32)
    labels[2,3],labels[5,9],labels[10,12]=42,257,65537
    source=np.full(labels.shape,80,np.uint8)
    payload=editor_assets(SimpleNamespace(original=source,labels=labels))
    rgb=decode_preview(payload['label_map']).astype(np.uint32)
    restored=rgb[...,0]+(rgb[...,1]<<8)+(rgb[...,2]<<16)
    np.testing.assert_array_equal(restored,labels)
    np.testing.assert_array_equal(decode_preview(payload['base_image']),np.repeat(source[...,None],3,axis=2))
    assert np.all(source==80)
    labels[0,0]=2**24
    with pytest.raises(ValueError,match='object IDs'):
        editor_assets(SimpleNamespace(original=source,labels=labels))


def test_local_id_sprite_has_plain_black_glyphs_and_no_leader_ink_on_fiber():
    labels=np.zeros((140,220),np.int32);labels[40:49,40:150]=1
    table=pd.DataFrame(dict(image_id=['x'],fiber_id=[1],review_status=['accepted']))
    item=fiber_id_layout(labels,table)[0]
    sprite=label_sprite(labels,item)
    leader=decode_preview(sprite['leader_image'])
    text=decode_preview(sprite['text_image'])
    mask=labels[sprite['y']:sprite['y']+leader.shape[0],sprite['x']:sprite['x']+leader.shape[1]]
    assert not leader[mask!=0,3].any()
    assert text[...,3].any() and np.any(text[...,3]==0)
    assert not text[...,:3].any() and not leader[...,:3].any()
