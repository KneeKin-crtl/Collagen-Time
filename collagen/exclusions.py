"""Full-resolution exclusion rectangles; proposals always require user review."""
import numbers
import numpy as np
from skimage.measure import label, regionprops
from .preprocessing import normalize


def validate_regions(regions, shape):
    height, width = shape[:2]
    checked = []
    for region in regions or ():
        if len(region) != 4 or any(isinstance(v, bool) or not isinstance(v, numbers.Integral) for v in region):
            raise ValueError('Exclusion bounds must be integer (left, top, right, bottom) pixels.')
        x0, y0, x1, y1 = map(int, region)
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError('Exclusion rectangle must have positive area inside the original image.')
        checked.append((x0, y0, x1, y1))
    return tuple(checked)


def overlap_pixels(component, bbox, regions):
    y0, x0, y1, x1 = bbox
    overlap = np.zeros(component.shape, dtype=bool)
    for left, top, right, bottom in regions:
        a, b, c, d = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
        if a < c and b < d:
            overlap[b-y0:d-y0, a-x0:c-x0] = True
    return int(np.count_nonzero(component & overlap))


def suggest_scale_bar_region(image):
    """Propose a bright rectangular panel in the lower-right corner.

    This heuristic does not identify collagen or infer calibration. Its returned
    coordinates must be checked against the displayed original by the operator.
    No region is excluded merely because this function returns a proposal.
    """
    gray = normalize(image)
    height, width = gray.shape
    y0, x0 = int(height * .8), int(width * .8)
    candidates = regionprops(label(gray[y0:, x0:] >= .93, connectivity=2))
    if not candidates:
        return None
    panel = max(candidates, key=lambda r: r.area)
    top, left, bottom, right = panel.bbox
    area = (bottom - top) * (right - left)
    if (panel.area < max(25, height * width * .00005) or panel.area / area < .55
            or right - left < width * .03 or bottom - top < height * .01):
        return None
    return (max(0, x0 + left - 2), max(0, y0 + top - 2),
            min(width, x0 + right + 2), min(height, y0 + bottom + 2))
