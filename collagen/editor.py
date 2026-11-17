"""Offline Streamlit component; browser sends original-pixel coordinates only."""
import base64
from io import BytesIO

import cv2
import numpy as np
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components

from .resources import resource_root

editor_path = resource_root() / 'assets' / 'fiber_editor'
fiber_editor = components.declare_component('fiber_editor', path=str(editor_path))


@st.cache_data(show_spinner=False, max_entries=2)
def image_url(rgb):
    output = BytesIO()
    Image.fromarray(rgb).save(output, format='PNG', compress_level=1)
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


@st.cache_data(show_spinner=False, max_entries=2)
def original_preview(image):
    """Encode decoded source pixels losslessly when PNG supports their dtype.

    Color-channel order changes only for OpenCV encoding. No normalization,
    annotations, resizing, or changes to the source array are applied to uint8
    or uint16 images. Other TIFF types require an explicitly labeled preview.
    """
    array = np.asarray(image)
    note = ''
    if array.dtype.kind == 'u' and array.dtype.itemsize in (1, 2):
        pixels = array.astype(np.uint8 if array.dtype.itemsize == 1 else np.uint16, copy=True)
    else:
        from .visualization import display_rgb
        pixels = display_rgb(array)
        note = ('Browser display conversion for this TIFF pixel type; original pixel '
                'values and resolution are preserved in analysis and downloads.')
    if pixels.ndim == 3:
        pixels = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGRA if pixels.shape[2] == 4 else cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode('.png', pixels, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not success:
        raise ValueError('Could not encode the original image preview.')
    return 'data:image/png;base64,' + base64.b64encode(encoded).decode('ascii'), note
