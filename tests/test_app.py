from io import BytesIO
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
    assert app.title[0].value == 'Collagen Fiber Analyzer'
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
        primary_images = app.main.get('image')
        assert len(primary_images) == 1
        assert 'green = included' in primary_images[0].proto.imgs[0].caption
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
        assert len(app.main.get('image')) == 5  # primary plus four detailed stages
        assert json.loads(app.json[0].value) == counts
        assert app.dataframe[0].value.equals(before)
        assert geometry.call_count == 1
        app.session_state['fiber_details'] = False
        app.run(timeout=30)
        assert len(app.main.get('image')) == 1
        assert geometry.call_count == 1
