import cv2
import numpy as np
from .preprocessing import normalize


def annotate(image, objects):
    gray = (normalize(image) * 255).astype(np.uint8)
    canvas = np.repeat(gray[..., None], 3, axis=2)
    for fiber_id, skeleton, centroid, accepted, *offsets in objects:
        color = (0, 255, 0) if accepted else (255, 120, 0)
        y0, x0 = offsets[0] if offsets else (0, 0)
        height, width = skeleton.shape
        canvas[y0:y0 + height, x0:x0 + width][skeleton] = color
        y, x = centroid
        cv2.putText(canvas, str(fiber_id), (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)
    return canvas
