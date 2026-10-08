import json
from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_app_initial_render():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'app.py').run(timeout=30)
    assert not app.exception
    assert app.title[0].value == 'Collagen Fiber Analyzer'


def test_app_analysis_render():
    # Inject an uploaded 16-bit microscopy image through the actual UI path.
    prefix = '''
from unittest.mock import patch
from io import BytesIO
import numpy as np
import tifffile
image = np.zeros((80, 120), np.uint16)
image[30:39, 15:100] = 45000
buffer = BytesIO()
tifffile.imwrite(buffer, image)
buffer.name = "sample.tiff"
def upload(label, **kwargs):
    return buffer if label.startswith("Microscopy") else None
patcher = patch("streamlit.file_uploader", side_effect=upload)
patcher.start()
'''
    app = AppTest.from_string(prefix + (Path(__file__).resolve().parents[1] / 'app.py').read_text() + '\npatcher.stop()').run(timeout=30)
    assert not app.exception
    assert not app.error
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0].review_status == 'accepted'


def test_app_imagej_report_with_missing_and_unmatched_measurements():
    prefix = '''
from unittest.mock import patch
from io import BytesIO
import numpy as np
import tifffile
image = np.zeros((80, 120), np.uint16)
image[5:12, :30] = 45000
image[30:39, 15:100] = 45000
buffer = BytesIO()
tifffile.imwrite(buffer, image)
buffer.name = "sample.tiff"
manual = BytesIO(b"image_id,fiber_id,length_um,mean_diameter_um\\nsample.tiff,1,30,7\\nsample.tiff,2,,8\\nsample.tiff,3,20,5\\n")
def upload(label, **kwargs):
    return buffer if label.startswith("Microscopy") else manual
patcher = patch("streamlit.file_uploader", side_effect=upload)
patcher.start()
'''
    app = AppTest.from_string(prefix + (Path(__file__).resolve().parents[1] / 'app.py').read_text() + '\npatcher.stop()').run(timeout=30)
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
    comparison = app.dataframe[2].value.set_index('fiber_id')
    assert comparison.loc[3, '_merge'] == 'right_only'
    assert comparison.loc[1, 'review_status'] == 'review_required'
    assert comparison.length_um_error.isna().all()
