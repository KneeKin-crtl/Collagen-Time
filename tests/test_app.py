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
