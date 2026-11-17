import cv2
import numpy as np
from .preprocessing import normalize
from .quality import counted_candidates

COUNTED_COLOR = (0, 255, 0)
EXCLUDED_COLOR = (255, 0, 0)


def display_rgb(image):
    """Return an RGB display copy, preserving uint8 colors and pixel positions."""
    array = np.asarray(image)
    if array.ndim == 2:
        array = np.repeat(array[..., None], 3, axis=2)
    elif array.ndim == 3 and array.shape[2] in (3, 4):
        array = array[..., :3]
    else:
        raise ValueError('Expected a grayscale or RGB(A) image.')
    if array.dtype == np.uint8:
        return array.copy()
    values = array.astype(float)
    if not np.isfinite(values).all():
        raise ValueError('Display image must contain finite pixels.')
    lo, hi = values.min(), values.max()
    if hi > lo:
        values = (values - lo) / (hi - lo)
    else:
        values = np.zeros_like(values)
    return np.rint(values * 255).astype(np.uint8)


def count_overlay(image, labels, table, opacity=.6):
    """Tint candidate pixels using the API's counting decisions, never the mask.

    Background is unchanged. This is the color layer only; accepted fiber ID
    callouts are composited separately for the primary view and exported images.
    Every label must have exactly one explicit API decision.
    """
    if not np.isfinite(opacity) or not 0 < opacity <= 1:
        raise ValueError('Overlay opacity must be finite and between 0 and 1.')
    labels = np.asarray(labels)
    if (labels.ndim != 2 or labels.shape != image.shape[:2]
            or labels.dtype.kind not in 'iu' or np.any(labels < 0)):
        raise ValueError('Candidate labels must be nonnegative integers at original resolution.')
    if not {'fiber_id', 'review_status'} <= set(table.columns):
        raise ValueError('The API fiber IDs and review statuses are required for the overlay.')
    if 'visible_in_overlay' in table:
        hidden = ~table.visible_in_overlay.astype(bool)
        if table.loc[hidden, 'review_status'].eq('accepted').any():
            raise ValueError('Accepted objects cannot be hidden in the review overlay.')
        table = table.loc[~hidden]
    if (table.fiber_id.isna().any() or table.fiber_id.duplicated().any()
            or not table.review_status.isin(['accepted', 'review_required']).all()):
        raise ValueError('Each candidate needs one unique ID and a valid API counting decision.')
    present = np.unique(labels)
    present = present[present != 0]
    if not np.array_equal(np.sort(table.fiber_id.to_numpy()), present):
        raise ValueError('Candidate labels and API decisions must describe exactly the same objects.')
    canvas = display_rgb(image)
    if table.empty:
        return canvas
    decisions = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    ids = table.fiber_id.to_numpy(dtype=np.intp)
    decisions[ids] = np.where(counted_candidates(table).to_numpy(), 1, 2)
    states = decisions[labels]
    for state, color in ((1, COUNTED_COLOR), (2, EXCLUDED_COLOR)):
        selected = states == state
        canvas[selected] = np.rint((1 - opacity) * canvas[selected]
                                  + opacity * np.asarray(color)).astype(np.uint8)
    return canvas


def annotate(image, objects, exclusion_regions=()):
    """Raw centerlines/regions; accepted ID callouts are a separate display layer."""
    gray = (normalize(image) * 255).astype(np.uint8)
    canvas = np.repeat(gray[..., None], 3, axis=2)
    for fiber_id, skeleton, centroid, accepted, *offsets in objects:
        color = COUNTED_COLOR if accepted else EXCLUDED_COLOR
        y0, x0 = offsets[0] if offsets else (0, 0)
        height, width = skeleton.shape
        canvas[y0:y0 + height, x0:x0 + width][skeleton] = color
    for left, top, right, bottom in exclusion_regions:
        cv2.rectangle(canvas, (left, top), (right - 1, bottom - 1), EXCLUDED_COLOR, 2)
    return canvas


def centerline_annotation(image, table, centerlines, exclusion_regions=()):
    """Render retained coordinates without redoing skeletons or diameter sampling."""
    gray = (normalize(image) * 255).astype(np.uint8)
    canvas = np.repeat(gray[..., None], 3, axis=2)
    for row in table.itertuples():
        yx = np.asarray(centerlines.get(row.fiber_id, ()), np.int32)
        if yx.size:
            canvas[yx[:,0],yx[:,1]] = COUNTED_COLOR if row.review_status == 'accepted' else EXCLUDED_COLOR
    for left, top, right, bottom in exclusion_regions:
        cv2.rectangle(canvas, (left,top), (right-1,bottom-1), EXCLUDED_COLOR, 2)
    return canvas
