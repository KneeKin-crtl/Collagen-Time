from io import BytesIO
import json
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
import pytest
import tifffile

from collagen.export import csv_bytes, result_archive
from collagen.feedback import FeedbackStore
from collagen.id_labels import fiber_id_layout, fiber_id_layer, composite_id_layer
from collagen.pipeline import analyze
from collagen.review import ReviewSession
from collagen.visualization import count_overlay


def review_fixture(tmp_path):
    image=np.zeros((160,260),np.uint16)
    image[30:39,30:140]=45000
    image[70:79,30:140]=45000
    image[110:119,:140]=45000  # Partial border object: green after override, never labeled.
    baseline=analyze(image,.25,'original.tiff')
    store=FeedbackStore(tmp_path/'review')
    return ReviewSession(baseline,store,'original.tiff')


def test_layout_uses_exported_ids_not_dataframe_indices_and_keeps_measurements():
    labels=np.zeros((180,260),np.int32)
    labels[30:39,30:140]=42
    labels[90:99,30:140]=7
    table=pd.DataFrame(dict(image_id=['sample.tif']*2,fiber_id=[42,7],
                            review_status=['accepted']*2,length_um=[7.3,11.8],
                            mean_diameter_um=[.9,2.3]),index=[900,800])
    before=table.copy(deep=True);before_labels=labels.copy()
    layout=fiber_id_layout(labels,table)
    assert [item['fiber_id'] for item in layout]==[7,42]
    assert [item['text'] for item in layout]==['7','42']
    csv=pd.read_csv(BytesIO(csv_bytes(table))).set_index('fiber_id')
    for item in layout:
        x,y=item['anchor_xy']
        assert labels[y,x]==item['fiber_id']
        assert item['image_id']=='sample.tif'
        assert csv.loc[item['fiber_id'],'length_um']==before.set_index('fiber_id').loc[item['fiber_id'],'length_um']
        assert csv.loc[item['fiber_id'],'mean_diameter_um']==before.set_index('fiber_id').loc[item['fiber_id'],'mean_diameter_um']
    pd.testing.assert_frame_equal(table,before)
    assert np.array_equal(labels,before_labels)


def test_labels_avoid_overlap_and_foreground_when_space_is_available():
    labels=np.zeros((300,500),np.int32)
    for i in range(6):
        y=30+i*40
        labels[y:y+7,180:260]=i+1
    table=pd.DataFrame(dict(fiber_id=list(range(1,7)),review_status=['accepted']*6))
    layout=fiber_id_layout(labels,table)
    assert fiber_id_layout(labels,table)==layout
    layer=fiber_id_layer(labels,layout)
    for i,item in enumerate(layout):
        x0,y0,x1,y1=item['box_xyxy']
        assert not labels[y0:y1,x0:x1].any()
        assert np.any(layer[y0:y1,x0:x1,:3]>200)  # Visible white glyph pixels.
        for other in layout[i+1:]:
            a,b,c,d=other['box_xyxy']
            assert max(0,min(x1,c)-max(x0,a))*max(0,min(y1,d)-max(y0,b))==0
    assert not layer[labels>0,3].any()  # Neither text nor leader covers these boundaries.


def test_only_measured_accepted_rows_get_labels_including_manual_overrides(tmp_path):
    r=review_fixture(tmp_path)
    assert [i['fiber_id'] for i in r.result.id_labels]==[1,2]
    r.correct(3,'accepted','training');r.refresh()
    assert r.result.summary['accepted_count']==3
    assert r.result.summary['accepted_measurement_count']==2
    assert [i['fiber_id'] for i in r.result.id_labels]==[1,2]
    r.correct(1,'rejected','training');r.refresh()
    assert [i['fiber_id'] for i in r.result.id_labels]==[2]
    r.correct(1,'accepted','training');r.refresh()
    assert [i['fiber_id'] for i in r.result.id_labels]==[1,2]


def test_review_ids_reopen_and_undo_without_recalculation_or_renumbering(tmp_path):
    r=review_fixture(tmp_path)
    before=r.result.table[['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']].copy(deep=True)
    with patch('collagen.review.measure',side_effect=AssertionError('Do not recalculate geometry.')):
        r.correct(1,'rejected','training');r.refresh()
        assert [i['fiber_id'] for i in r.result.id_labels]==[2]
        reopened=ReviewSession(r.baseline,FeedbackStore(tmp_path/'review'),'original.tiff')
        assert reopened.analysis_key==r.analysis_key
        assert reopened.result.id_labels==r.result.id_labels
        reopened.store.undo(reopened.analysis_key);reopened.refresh()
        assert [i['fiber_id'] for i in reopened.result.id_labels]==[1,2]
        pd.testing.assert_frame_equal(before,reopened.result.table[before.columns])
    assert np.array_equal(r.result.labels,r.baseline.labels)


def test_manual_outline_id_is_preserved_and_hidden_on_undo(tmp_path):
    r=review_fixture(tmp_path)
    new_id=r.add_outline([[180,30],[230,30],[230,40],[180,40]],'training')
    r.refresh()
    assert new_id==4
    assert [i['fiber_id'] for i in r.result.id_labels]==[1,2,4]
    r.store.undo(r.analysis_key);r.refresh()
    assert [i['fiber_id'] for i in r.result.id_labels]==[1,2]
    assert r.result.table.fiber_id.tolist()==[1,2,3]


def test_exported_annotation_and_id_manifest_match_excel_compatible_rows(tmp_path):
    r=review_fixture(tmp_path)
    r.correct(1,'rejected','training');r.refresh()
    before_csv=csv_bytes(r.result.table)
    layer=fiber_id_layer(r.result.labels,r.result.id_labels)
    raw=count_overlay(r.result.original,r.result.labels,r.result.table)
    labeled=composite_id_layer(raw,layer)
    with zipfile.ZipFile(BytesIO(result_archive(r.result))) as archive:
        csv=pd.read_csv(BytesIO(archive.read('fibers.csv')))
        manifest=json.loads(archive.read('fiber_id_labels.json'))
        expected=csv[csv.included_in_measurement_statistics].fiber_id.tolist()
        assert [i['fiber_id'] for i in manifest]==expected==[2]
        assert manifest==r.result.id_labels
        assert np.array_equal(tifffile.imread(BytesIO(archive.read('count_overlay.tiff'))),labeled)
        assert np.array_equal(tifffile.imread(BytesIO(archive.read('annotated.tiff'))),r.result.annotated)
        assert np.array_equal(tifffile.imread(BytesIO(archive.read('original.tiff'))),r.result.original)
        assert np.array_equal(tifffile.imread(BytesIO(archive.read('labels.tiff'))),r.result.labels)
    assert before_csv==csv_bytes(r.result.table)
    assert not np.array_equal(raw,labeled)


def test_rendered_text_is_not_used_as_geometry(tmp_path):
    r=review_fixture(tmp_path)
    before=r.result.annotated.copy()
    # An arbitrary display image must not change the retained centerlines.
    r.baseline.annotated[:]=(0,255,0)
    r.refresh()
    assert np.array_equal(r.result.annotated,before)


def test_empty_or_fully_rejected_images_have_no_id_ink():
    labels=np.zeros((60,100),np.int32);labels[20:30,20:80]=1
    table=pd.DataFrame(dict(fiber_id=[1],review_status=['review_required']))
    assert fiber_id_layout(labels,table)==[]
    assert not fiber_id_layer(labels,[]).any()


def test_missing_measurable_id_fails_rather_than_labeling_another_object():
    labels=np.zeros((60,100),np.int32);labels[20:30,20:80]=3
    table=pd.DataFrame(dict(fiber_id=[12],review_status=['accepted']))
    with pytest.raises(ValueError,match='missing from the instance mask'):
        fiber_id_layout(labels,table)
