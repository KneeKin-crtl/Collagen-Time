"""Offline Streamlit component; browser sends original-pixel coordinates only."""
import base64
from io import BytesIO

from PIL import Image
import streamlit.components.v1 as components

from .resources import resource_root

editor_path = resource_root() / 'assets' / 'fiber_editor'
fiber_editor = components.declare_component('fiber_editor', path=str(editor_path))


def image_url(rgb):
    output = BytesIO()
    Image.fromarray(rgb).save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
