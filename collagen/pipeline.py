from dataclasses import dataclass
import json
import numpy as np
import pandas as pd
from .preprocessing import preprocess
from .segmentation import ThresholdSegmenter
from .detection import detect
from .geometry import measure
from .quality import classify
from .visualization import annotate

COLUMNS = ["image_id", "fiber_id", "calibration_um_per_px", "area_px", "length_um", "mean_diameter_um", "diameter_sd_um", "diameter_samples", "endpoints", "border_touch", "crossing_merge", "review_status", "qc_flags", "diameter_method"]

@dataclass
class Analysis:
    original: np.ndarray
    mask: np.ndarray
    labels: np.ndarray
    annotated: np.ndarray
    table: pd.DataFrame
    summary: dict


def analyze(image, calibration, image_id="image", segmenter=None, sigma=0.0, min_area=20, exclusion_px=2.0):
    if not np.isfinite(calibration) or calibration <= 0:
        raise ValueError("Calibration must be finite and positive.")
    if min_area < 1:
        raise ValueError("Minimum area must be positive.")
    gray = preprocess(image, sigma)
    mask = np.asarray((segmenter or ThresholdSegmenter())(gray))
    if mask.shape != gray.shape or mask.dtype != bool:
        raise ValueError("Segmenter must return a boolean mask at original resolution.")
    labels, regions = detect(mask)
    rows, objects = [], []
    for region in regions:
        component = labels == region.label
        m = measure(component, calibration, exclusion_px)
        border = bool(component[0].any() or component[-1].any() or component[:, 0].any() or component[:, -1].any())
        flags, status = classify(m, border, region.area, min_area)
        rows.append(dict(image_id=image_id, fiber_id=region.label, calibration_um_per_px=calibration,
                         area_px=int(region.area), length_um=m["length_um"], mean_diameter_um=m["mean_diameter_um"],
                         diameter_sd_um=m["diameter_sd_um"], diameter_samples=m["diameter_samples"],
                         endpoints=json.dumps([[int(x), int(y)] for y, x in m["endpoints"]]),
                         border_touch=border, crossing_merge=bool(m["junctions"]), review_status=status,
                         qc_flags=";".join(flags), diameter_method="2x_distance_transform_approximation"))
        objects.append((region.label, m["skeleton"], region.centroid, status == "accepted"))
    table = pd.DataFrame(rows, columns=COLUMNS)
    accepted = table[table.review_status == "accepted"]
    summary = {"candidate_count": len(table), "accepted_isolated_count": len(accepted),
               "review_required_count": len(table) - len(accepted),
               "accepted_mean_length_um": float(accepted.length_um.mean()),
               "accepted_mean_diameter_um": float(accepted.mean_diameter_um.mean())}
    return Analysis(image, mask, labels, annotate(image, objects), table, summary)
