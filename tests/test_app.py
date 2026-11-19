from io import BytesIO
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from openpyxl import Workbook
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
import tifffile

APP = Path(__file__).resolve().parents[1] / 'app.py'


def image_bytes(image):
    buffer = BytesIO()
    tifffile.imwrite(buffer, image)
    return buffer.getvalue()


@pytest.fixture
def uploads(monkeypatch):
    st.cache_data.clear()
    files = {}

    def upload(label, **kwargs):
        key = 'image' if label.startswith('Microscopy') else 'manual' if label.startswith('Optional') else 'workbook'
        if key not in files:
            return None
        name, data = files[key]
        result = BytesIO(data)
        result.name = name
        return result

    monkeypatch.setattr(st, 'file_uploader', upload)
    return files


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


def submit(app, calibration=.5, bright=True):
    widget(app, 'number_input', 'Calibration (µm/pixel)').set_value(calibration)
    widget(app, 'checkbox', 'Bright fibers on dark background').set_value(bright)
    widget(app, 'button', 'Analyze image').click().run(timeout=30)
    return app


def test_app_initial_render(uploads):
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception
    assert any('<h1>Collagen Fiber Analyzer</h1>' in e.proto.body for e in app.get('html'))
    assert widget(app, 'number_input', 'Calibration (µm/pixel)').value is None


def test_analysis_requires_calibration_and_explicit_submission(uploads):
    image = np.zeros((80, 120), np.uint16)
    image[30:39, 15:100] = 45000
    uploads['image'] = ('sample.tiff', image_bytes(image))
    app = AppTest.from_file(APP).run(timeout=30)
    assert len(app.dataframe) == 0
    submit(app, None)
    assert not app.exception
    assert 'calibration' in app.error[0].value
    assert not app.dataframe
    submit(app)
    assert not app.exception
    assert not app.error
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0].review_status == 'accepted'


def test_cache_reuses_geometry_and_archive_is_lazy(uploads):
    from collagen.pipeline import analyze_pixels
    from collagen.export import result_archive
    image = np.zeros((80, 120), np.uint16)
    image[30:39, 15:100] = 45000
    uploads['image'] = ('cache.tiff', image_bytes(image))
    with patch('collagen.pipeline.analyze_pixels', wraps=analyze_pixels) as geometry, patch('collagen.export.result_archive', wraps=result_archive) as archive:
        app = AppTest.from_file(APP).run(timeout=30)
        assert geometry.call_count == archive.call_count == 0
        submit(app, .5)
        first_length = app.dataframe[0].value.iloc[0].length_um
        assert geometry.call_count == 1
        assert archive.call_count == 0
        app.run(timeout=30)
        assert geometry.call_count == 1
        submit(app, .25)
        assert geometry.call_count == 1
        assert app.dataframe[0].value.iloc[0].length_um == pytest.approx(first_length / 2)
        widget(app, 'button', 'Prepare QC archive').click().run(timeout=30)
        assert archive.call_count == 1
        app.run(timeout=30)
        assert archive.call_count == 1
        assert 'archive' in app.session_state
        submit(app, .125)
        assert 'archive' not in app.session_state
        assert geometry.call_count == 1


def test_new_image_needs_its_own_calibration_and_clears_old_results(uploads):
    image = np.zeros((80, 120), np.uint16)
    image[30:39, 15:100] = 45000
    uploads['image'] = ('first.tiff', image_bytes(image))
    app = submit(AppTest.from_file(APP).run(timeout=30))
    assert app.dataframe
    app.session_state['fiber_details'] = True
    app.run(timeout=30)
    image[10:15, 10:40] = 45000
    uploads['image'] = ('second.tiff', image_bytes(image))
    app.run(timeout=30)
    assert not app.dataframe
    assert widget(app, 'number_input', 'Calibration (µm/pixel)').value is None
    submit(app)
    details = next(e for e in app.expander if e.label == 'View Detailed Fiber Analysis')
    assert not details.proto.expanded


def test_scale_bar_proposal_requires_confirmation_and_preserves_candidates(uploads):
    image = np.full((240, 400), 128, np.uint8)
    image[100:105, 50:130] = 0
    image[200:230, 330:395] = 255
    image[208:212, 340:385] = 0
    uploads['image'] = ('panel.tiff', image_bytes(image))
    app = AppTest.from_file(APP).run(timeout=30)
    assert widget(app, 'selectbox', 'Scale-bar review').value == 'Review required'
    submit(app, .2, bright=False)
    assert app.error
    assert not app.dataframe
    widget(app, 'selectbox', 'Scale-bar review').set_value('No exclusion needed (reviewed)')
    submit(app, .2, bright=False)
    assert json.loads(app.json[0].value)['accepted_isolated_count'] == 2
    widget(app, 'selectbox', 'Scale-bar review').set_value('Exclude rectangle')
    submit(app, .2, bright=False)
    assert not app.exception
    assert not app.error
    assert json.loads(app.json[0].value)['accepted_isolated_count'] == 1
    assert len(app.dataframe[0].value) == 2
    assert app.dataframe[0].value.excluded_region.sum() == 1


@pytest.mark.parametrize('data', [b'', b'not a PNG'])
def test_invalid_image_has_clear_error_without_traceback(uploads, data):
    uploads['image'] = ('bad.png', data)
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception
    assert app.error
    assert not app.dataframe


def test_app_imagej_report_with_missing_and_unmatched_measurements(uploads):
    image = np.zeros((80, 120), np.uint16)
    image[5:12, :30] = 45000
    image[30:39, 15:100] = 45000
    uploads['image'] = ('sample.tiff', image_bytes(image))
    uploads['manual'] = ('manual.csv', b'image_id,fiber_id,length_um,mean_diameter_um\nsample.tiff,1,30,7\nsample.tiff,2,,8\nsample.tiff,3,20,5\n')
    app = submit(AppTest.from_file(APP).run(timeout=30), calibration=1.)
    assert not app.exception
    assert not app.error
    counts = json.loads(app.json[1].value)
    assert counts['matched_count'] == 2
    assert counts['accepted_matched_count'] == 1
    assert counts['review_required_matched_count'] == 1
    assert counts['unmatched_manual_count'] == 1
    stats = app.dataframe[1].value.set_index('measurement')
    assert stats.loc['length_um', 'paired_count'] == 0
    assert stats.loc['mean_diameter_um', 'paired_count'] == 1
    assert app.dataframe[2].value.set_index('fiber_id').loc[3, '_merge'] == 'right_only'


def test_app_workbook_review_without_an_image(uploads):
    book = Workbook()
    sheet = book.active
    sheet.title = 'RT30min Length'
    sheet.append(['Image 1', 'Length'])
    sheet.append([1, 12.])
    sheet.append([None, 12.])
    buffer = BytesIO()
    book.save(buffer)
    uploads['workbook'] = ('manual.xlsx', buffer.getvalue())
    app = AppTest.from_file(APP).run(timeout=30)
    assert not app.exception
    assert not app.error
    assert app.dataframe[0].value.iloc[0].measurement_count == 1
    assert len(app.dataframe[1].value) == 1


def test_primary_overlay_and_lazy_details_follow_actual_counts(uploads):
    from collagen.pipeline import analyze_pixels
    image = np.zeros((80, 120), np.uint16)
    image[5:12, :30] = 45000  # border-truncated: not counted
    image[30:39, 40:110] = 45000  # isolated: counted
    uploads['image'] = ('overlay-layout.tiff', image_bytes(image))
    with patch('collagen.pipeline.analyze_pixels', wraps=analyze_pixels) as geometry:
        app = submit(AppTest.from_file(APP).run(timeout=30))
        assert not app.exception
        assert not app.error
        counts = json.loads(app.json[0].value)
        assert counts['accepted_isolated_count'] == 1
        assert counts['review_required_count'] == 1
        assert len(app.main.get('image')) == 0
        editors = app.main.get('component_instance')
        assert len(editors) == 1
        assert 'fiber_editor' in editors[0].proto.component_name
        editor_args = json.loads(editors[0].proto.json_args)
        assert editor_args['source_image']==dict(name='overlay-layout.tiff',
            sha256=hashlib.sha256(uploads['image'][1]).hexdigest(),width=120,height=80)
        assert any(c.value=='Loaded image: overlay-layout.tiff · 120 × 80 pixels' for c in app.caption)
        assert editor_args['base_image'].startswith('data:image/png;base64,')
        from test_editor import decode_preview
        np.testing.assert_array_equal(decode_preview(editor_args['original_image']), image)
        assert editor_args['original_note'] == ''
        assert editor_args['label_map'].startswith('data:image/png;base64,')
        assert [o['fiber_id'] for o in editor_args['objects'] if o['accepted']]==[2]
        assert [s['fiber_id'] for s in editor_args['id_sprites']]==[2]
        assert [i['fiber_id'] for i in editor_args['fiber_id_labels']]==[2]
        assert editor_args['context'] == app.session_state['review_context']
        details = next(e for e in app.expander if e.label == 'View Detailed Fiber Analysis')
        assert not details.proto.expanded
        assert len(details.get('image')) == 0
        before = app.dataframe[0].value.copy(deep=True)
        app.session_state['fiber_details'] = True
        app.run(timeout=30)
        assert not app.exception
        details = next(e for e in app.expander if e.label == 'View Detailed Fiber Analysis')
        assert details.proto.expanded
        assert len(details.get('image')) == 4
        assert len(app.main.get('image')) == 4
        assert len(app.main.get('component_instance')) == 1  # interactive primary plus four stages
        assert json.loads(app.json[0].value) == counts
        assert app.dataframe[0].value.equals(before)
        assert geometry.call_count == 1
        app.session_state['fiber_details'] = False
        app.run(timeout=30)
        assert len(app.main.get('image')) == 0
        assert len(app.main.get('component_instance')) == 1
        assert geometry.call_count == 1


def test_comparison_original_stays_unchanged_on_review_and_changes_with_upload(uploads):
    from test_editor import decode_preview
    image = np.full((80, 120), 2000, np.uint16)
    image[30:39, 15:100] = 45000
    uploads['image'] = ('first.tiff', image_bytes(image))
    app = submit(AppTest.from_file(APP).run(timeout=30))
    before = app.dataframe[0].value.copy(deep=True)
    args = json.loads(app.main.get('component_instance')[0].proto.json_args)
    original_url = args['original_image']
    widget(app, 'button', 'Mark rejected').click().run(timeout=30)
    rejected = json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert rejected['base_image'] == args['base_image']
    assert rejected['label_map'] == args['label_map']
    assert rejected['objects'][0]['accepted'] is False
    assert args['objects'][0]['accepted'] is True
    assert rejected['original_image'] == original_url
    widget(app, 'button', 'Undo last correction').click().run(timeout=30)
    measurement_columns = ['fiber_id', 'length_um', 'mean_diameter_um', 'diameter_sd_um',
                           'included_in_measurement_statistics']
    assert app.dataframe[0].value[measurement_columns].equals(before[measurement_columns])
    assert json.loads(app.main.get('component_instance')[0].proto.json_args)['original_image'] == original_url
    # A different source of the same dimensions must never retain the old preview.
    image[0, 0] = 1234
    uploads['image'] = ('second.tiff', image_bytes(image))
    app.run(timeout=30)
    submit(app)
    assert not app.exception and not app.error
    replacement = json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert replacement['source_image']['name']=='second.tiff'
    assert replacement['source_image']['sha256']==hashlib.sha256(uploads['image'][1]).hexdigest()
    assert replacement['source_image']['sha256']!=args['source_image']['sha256']
    assert replacement['original_image'] != original_url
    np.testing.assert_array_equal(decode_preview(replacement['original_image']), image)


def test_comparison_remains_available_when_review_storage_is_unwritable(uploads):
    from test_editor import decode_preview
    image = np.zeros((80, 120), np.uint16)
    image[30:39, 15:100] = 45000
    uploads['image'] = ('readonly.tiff', image_bytes(image))
    with patch('collagen.review_ui.FeedbackStore', side_effect=OSError('Read only')):
        app = submit(AppTest.from_file(APP).run(timeout=30))
    assert not app.exception
    assert 'Manual review storage is unavailable' in app.error[0].value
    args = json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert args['review_enabled'] is False
    np.testing.assert_array_equal(decode_preview(args['original_image']), image)
    assert args['base_image'] and args['label_map'] and args['id_sprites']
    assert len(app.dataframe[0].value) == 1
    old_asset_key=args['asset_key']
    image[0,0]=1
    uploads['image']=('another-readonly.tiff',image_bytes(image))
    with patch('collagen.review_ui.FeedbackStore', side_effect=OSError('Read only')):
        app.run(timeout=30)
        submit(app)
    replacement=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert replacement['asset_key']!=old_asset_key
    np.testing.assert_array_equal(decode_preview(replacement['original_image']),image)


def test_manual_buttons_update_csv_counts_restore_on_reopen_and_undo(uploads):
    from collagen.pipeline import analyze_pixels
    image=np.zeros((80,120),np.uint16)
    image[30:39,15:100]=45000
    uploads['image']=('review.tiff',image_bytes(image))
    with patch('collagen.pipeline.analyze_pixels',wraps=analyze_pixels) as geometry:
        app=submit(AppTest.from_file(APP).run(timeout=30))
        length=app.dataframe[0].value.length_um.iloc[0]
        widget(app,'button','Prepare QC archive').click().run(timeout=30)
        assert 'archive' in app.session_state
        widget(app,'button','Mark rejected').click().run(timeout=30)
        assert not app.exception and not app.error
        assert app.dataframe[0].value.classification.tolist()==['rejected']
        args=json.loads(app.main.get('component_instance')[0].proto.json_args)
        assert args['fiber_id_labels']==[]
        assert json.loads(app.json[0].value)['accepted_measurement_count']==0
        assert 'archive' not in app.session_state
        assert geometry.call_count==1
        reopened=submit(AppTest.from_file(APP).run(timeout=30))
        assert reopened.dataframe[0].value.classification.tolist()==['rejected']
        widget(reopened,'button','Undo last correction').click().run(timeout=30)
        assert reopened.dataframe[0].value.classification.tolist()==['accepted']
        args=json.loads(reopened.main.get('component_instance')[0].proto.json_args)
        assert [i['fiber_id'] for i in args['fiber_id_labels']]==[1]
        assert reopened.dataframe[0].value.length_um.iloc[0]==length
        assert json.loads(reopened.json[0].value)['accepted_measurement_count']==1
        assert geometry.call_count==1


def test_editor_event_updates_one_object_and_stale_replay_does_not_toggle_twice(uploads):
    image=np.zeros((100,160),np.uint16)
    image[20:29,20:120]=45000
    image[40:49,20:120]=45000
    uploads['image']=('click.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    event=dict(action='toggle',token='ui-click-1',context=app.session_state['review_context'],x=25,y=24)
    with patch('collagen.review_ui.fiber_editor',return_value=event):
        app.run(timeout=30)
        assert not app.exception
        assert app.dataframe[0].value.classification.tolist()==['rejected','accepted']
        app.run(timeout=30)
        assert app.dataframe[0].value.classification.tolist()==['rejected','accepted']
    bad={**event,'token':'different-token'}
    with patch('collagen.review_ui.fiber_editor',return_value=bad):
        app.run(timeout=30)
        assert 'older image or review' in app.warning[0].value
        assert app.dataframe[0].value.classification.tolist()==['rejected','accepted']


def test_retrain_without_enough_reviews_explains_requirements(uploads):
    image=np.zeros((80,120),np.uint16);image[30:39,15:100]=45000
    uploads['image']=('small.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    app.session_state['feedback_learning']=True
    app.run(timeout=30)
    widget(app,'button','Retrain Model Using Corrections').click()
    # AppTest does not serialize expander state like the browser does.
    app.session_state['feedback_learning']=True
    app.run(timeout=30)
    assert not app.exception and not app.error
    assert app.warning, repr(app.session_state)
    assert 'two training images' in app.warning[0].value


def test_object_id_review_cursor_advances_without_changing_geometry(uploads):
    image=np.zeros((100,160),np.uint16)
    image[20:29,20:120]=45000;image[40:49,20:120]=45000
    uploads['image']=('guided.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    fields=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    before=app.dataframe[0].value[fields].copy()
    assert widget(app,'selectbox','Object ID').value==1
    widget(app,'selectbox','Object ID').set_value(2).run(timeout=30)
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert args['selection_request']['fiber_id']==2
    assert app.dataframe[0].value.manual_classification.isna().all()
    widget(app,'button','Mark rejected').click().run(timeout=30)
    assert not app.exception
    assert widget(app,'selectbox','Object ID').value==1
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert args['selection_request']['fiber_id']==1
    widget(app,'button','Mark accepted').click().run(timeout=30)
    assert not app.exception
    assert widget(app,'selectbox','Object ID').value is None
    assert widget(app,'button','Mark accepted').disabled
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert args['selection_request']['fiber_id'] is None
    assert app.dataframe[0].value.manual_classification.tolist()==['accepted','rejected']
    assert app.dataframe[0].value[fields].equals(before)


def test_canvas_selection_event_only_updates_cursor_not_saved_reviews(uploads):
    image=np.zeros((100,160),np.uint16)
    image[20:29,20:120]=45000;image[40:49,20:120]=45000
    uploads['image']=('cursor.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    review=app.session_state['_active_review']
    revision=review.store.revision
    before=app.dataframe[0].value.copy()
    event=dict(action='select',token='cursor-only',context=review.context,
               fiber_id=2,guided=True,view_mode='all')
    with patch('collagen.review_ui.fiber_editor',return_value=event):
        app.run(timeout=30)
    assert not app.exception
    assert widget(app,'selectbox','Object ID').value==2
    assert review.store.revision==revision
    assert app.dataframe[0].value.equals(before)
    with patch('collagen.review_ui.fiber_editor',return_value={**event,'token':'bad-cursor','fiber_id':999}):
        app.run(timeout=30)
    assert not app.exception
    assert widget(app,'selectbox','Object ID').value==2
    assert review.store.revision==revision
    assert 'active visible object' in app.warning[0].value


def test_physical_filters_change_eligibility_without_geometry_or_image_encoding(uploads):
    from collagen.pipeline import analyze_pixels
    from collagen.editor import editor_assets
    image=np.zeros((100,160),np.uint16);image[20:29,20:120]=45000
    uploads['image']=('filters.tiff',image_bytes(image))
    with patch('collagen.pipeline.analyze_pixels',wraps=analyze_pixels) as geometry, \
         patch('collagen.review_ui.editor_assets',wraps=editor_assets) as encoding:
        app=submit(AppTest.from_file(APP).run(timeout=30),calibration=.01)
        measurements=app.dataframe[0].value[['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']].copy()
        context=app.session_state['review_context']
        widget(app,'number_input','Minimum reliable mean width (pixels)').set_value(100.)
        submit(app,calibration=.01)
        assert app.dataframe[0].value.selection_category.tolist()==['too_small']
        assert json.loads(app.json[0].value)['accepted_measurement_count']==0
        assert app.session_state['review_context']!=context
        widget(app,'button','Mark accepted').click().run(timeout=30)
        assert app.dataframe[0].value.manual_classification.tolist()==['accepted']
        assert app.dataframe[0].value.review_status.tolist()==['review_required']
        assert app.warning and 'green approved masks are excluded from measurements' in app.warning[0].value
        assert app.dataframe[0].value.review_approved.tolist()==[True]
        widget(app,'number_input','Minimum reliable mean width (pixels)').set_value(3.)
        widget(app,'checkbox','Enable research-interest diameter range').check()
        submit(app,calibration=.01)
        assert app.dataframe[0].value.selection_category.tolist()==['accepted']
        assert app.dataframe[0].value.automatic_category.tolist()==['out_of_range']
        assert app.dataframe[0].value[measurements.columns].equals(measurements)
        assert geometry.call_count==encoding.call_count==1
        args=json.loads(app.main.get('component_instance')[0].proto.json_args)
        assert args['objects'][0]['accepted'] and args['objects'][0]['manual']=='accepted'


def test_ambiguous_approval_reports_actual_geometry_and_offers_existing_outline(uploads):
    image=np.zeros((100,160),np.uint16)
    image[40:49,20:140]=45000;image[20:75,70:79]=45000
    uploads['image']=('crossing.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    before=app.dataframe[0].value.copy(deep=True)
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    event=dict(action='accept',token='approve-crossing',context=args['context'],x=30,y=44,fiber_id=1)
    with patch('collagen.review_ui.fiber_editor',return_value=event):app.run(timeout=30)
    app.run(timeout=30)
    assert not app.exception
    row=app.dataframe[0].value.iloc[0]
    assert row.manual_classification=='accepted' and not row.included_in_measurement_statistics
    assert np.isnan(row.length_um) and np.isnan(row.mean_diameter_um)
    assert 'not included in measurements' in app.success[0].value
    assert 'crossing or branching geometry' in app.success[0].value
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    obj=args['objects'][0]
    assert obj['can_outline'] and not obj['can_accept'] and not obj['accepted']
    assert obj['approved']
    assert '4 endpoints' in obj['accept_block_message']
    geometry=['fiber_id','length_um','mean_diameter_um','diameter_sd_um','endpoints']
    assert app.dataframe[0].value[geometry].equals(before[geometry])


def test_browser_asset_acknowledgment_uses_references_and_cache_miss_restores_layers(uploads):
    image=np.zeros((80,120),np.uint16);image[30:39,15:100]=45000
    uploads['image']=('assets.tiff',image_bytes(image))
    app=submit(AppTest.from_file(APP).run(timeout=30))
    args=json.loads(app.main.get('component_instance')[0].proto.json_args)
    event=dict(action='reject',token='asset-ack-event',context=args['context'],fiber_id=1,
               asset_key=args['asset_key'],known_sprites=[args['id_sprites'][0]['sprite_key']])
    with patch('collagen.review_ui.fiber_editor',return_value=event):app.run(timeout=30)
    app.run(timeout=30)
    referenced=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert referenced['asset_key']==args['asset_key']
    assert 'original_image' not in referenced and 'base_image' not in referenced and 'label_map' not in referenced
    reload=dict(action='request_assets',token='asset-cache-miss',context=referenced['context'])
    with patch('collagen.review_ui.fiber_editor',return_value=reload):app.run(timeout=30)
    app.run(timeout=30)
    restored=json.loads(app.main.get('component_instance')[0].proto.json_args)
    assert restored['original_image']==args['original_image'] and restored['label_map']==args['label_map']
    assert restored['objects'][0]['manual']=='rejected'
