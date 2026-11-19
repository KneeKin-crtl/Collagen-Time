import base64

import numpy as np
import pytest
from types import SimpleNamespace
import pandas as pd

from collagen.editor import (original_preview, editor_assets, label_sprite, editor_objects,
                             centerline_midpoint, review_focus_points)
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


@pytest.mark.parametrize('saved_event', [None, np.nan, pd.NA])
def test_unreviewed_editor_records_have_a_json_safe_confirmation_marker(saved_event):
    table=pd.DataFrame([dict(fiber_id=1855,review_status='accepted',last_correction_id=saved_event,
                             length_um=50.,mean_diameter_um=3.)])
    assert editor_objects(table)[0]['saved_event']==0


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


def test_review_anchor_follows_weighted_centerline_instead_of_label_or_box():
    diagonal=np.array([[i,i] for i in range(11)])
    horizontal=np.array([[10,x] for x in range(11,31)])
    points=np.r_[diagonal,horizontal]
    # Unordered stored skeleton pixels must follow path arc length, including
    # diagonal sqrt(2) steps. This is display geometry only, not a new measure.
    expected=[10+(20-10*np.sqrt(2))/2+.5,10.5]
    assert centerline_midpoint(points[::-1])==pytest.approx(expected)
    assert centerline_midpoint(diagonal)==pytest.approx([5.5,5.5])


def test_curved_border_and_ambiguous_navigation_anchors_preserve_source_geometry():
    labels=np.zeros((90,140),np.int32)
    line=np.r_[np.array([[10,x] for x in range(10,111)]),
               np.array([[y,110] for y in range(11,61)])]
    labels[line[:,0],line[:,1]]=1
    labels[0,:10]=2
    # Representative mask fallback must be on the object, not the hollow box.
    labels[70:80,20]=3;labels[79,20:90]=3;labels[70:80,89]=3
    source=np.full(labels.shape,80,np.uint8)
    table=pd.DataFrame(dict(fiber_id=[1,2,3],length_um=[15.,.9,np.nan]))
    result=SimpleNamespace(labels=labels,original=source,table=table,
                           centerlines={1:line,2:np.array([[0,x] for x in range(10)])})
    before=labels.copy();rows=table.copy()
    anchors=review_focus_points(result)
    assert anchors[0]==dict(fiber_id=1,x=85.5,y=10.5,source='centerline_midpoint')
    assert anchors[1]==dict(fiber_id=2,x=5.,y=.5,source='centerline_midpoint')
    assert anchors[2]['source']=='mask_representative'
    assert labels[int(anchors[2]['y']),int(anchors[2]['x'])]==3
    np.testing.assert_array_equal(labels,before);pd.testing.assert_frame_equal(table,rows)
    assert np.all(source==80)


@pytest.mark.parametrize('points',[
    [[0,0]], [[0,0],[0,1],[0,2],[1,1]],
    [[0,0],[0,1],[1,1],[1,0]], [[0,0],[0,1],[5,5],[5,6]]])
def test_unavailable_or_ambiguous_centerline_uses_mask_fallback(points):
    assert centerline_midpoint(np.array(points)) is None
