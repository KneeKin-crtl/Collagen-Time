from dataclasses import dataclass, field
import json
import numpy as np
import pandas as pd
from .preprocessing import preprocess
from .segmentation import ThresholdSegmenter, segmentation_parameters
from .detection import detect
from .geometry import measure
from .quality import classify, counted_candidates
from .visualization import annotate
from .exclusions import overlap_pixels, validate_regions
from .id_labels import fiber_id_layout, fiber_id_layer, composite_id_layer

COLUMNS = ["image_id", "fiber_id", "calibration_um_per_px", "area_px", "length_um", "mean_diameter_um", "diameter_sd_um", "diameter_samples", "endpoints", "border_touch", "crossing_merge", "review_status", "qc_flags", "diameter_method", "excluded_region", "exclusion_overlap_px"]
DISTANCE_FIELDS = ['length', 'mean_diameter', 'diameter_sd']
PIXEL_COLUMNS = [c.replace('_um', '_px') for c in COLUMNS if c != 'calibration_um_per_px']


@dataclass
class Analysis:
    original: np.ndarray
    mask: np.ndarray
    labels: np.ndarray
    annotated: np.ndarray
    table: pd.DataFrame
    summary: dict
    parameters: dict = field(default_factory=dict)
    baseline_labels: np.ndarray | None = None
    manual_masks: list = field(default_factory=list)
    centerlines: dict = field(default_factory=dict)
    id_labels: list | None = None


@dataclass
class PixelAnalysis:
    """Calibration-independent measurements, suitable for a bounded UI cache."""
    original: np.ndarray
    mask: np.ndarray
    labels: np.ndarray
    annotated: np.ndarray
    table: pd.DataFrame
    parameters: dict
    centerlines: dict = field(default_factory=dict)


def analyze_pixels(image, image_id="image", segmenter=None, sigma=0.0, min_area=20,
                   exclusion_px=2.0, exclusion_regions=()):
    if min_area < 1:
        raise ValueError("Minimum area must be positive.")
    if not np.isfinite(exclusion_px) or exclusion_px < 0:
        raise ValueError('Endpoint exclusion must be finite and nonnegative.')
    regions_to_exclude = validate_regions(exclusion_regions, image.shape)
    segmenter = segmenter or ThresholdSegmenter()
    gray = preprocess(image, sigma)
    mask = np.asarray(segmenter(gray))
    if mask.shape != gray.shape or mask.dtype != bool:
        raise ValueError("Segmenter must return a boolean mask at original resolution.")
    labels, regions = detect(mask)
    rows, objects, centerlines = [], [], {}
    for region in regions:
        component = region.image
        y0, x0, y1, x1 = region.bbox
        m = measure(component, 1.0, exclusion_px)
        border = y0 == 0 or x0 == 0 or y1 == mask.shape[0] or x1 == mask.shape[1]
        flags, status = classify(m, border, region.area, min_area)
        overlap = overlap_pixels(component, region.bbox, regions_to_exclude) if regions_to_exclude else 0
        if overlap:
            # Preserve the entire candidate; cutting it would fabricate endpoints.
            flags.append('excluded_region')
            status = 'review_required'
        rows.append(dict(image_id=image_id, fiber_id=region.label,
                         area_px=int(region.area), length_px=m['length_um'],
                         mean_diameter_px=m['mean_diameter_um'], diameter_sd_px=m['diameter_sd_um'],
                         diameter_samples=m['diameter_samples'],
                         endpoints=json.dumps([[int(x + x0), int(y + y0)] for y, x in m['endpoints']]),
                         border_touch=border, crossing_merge=bool(m['junctions']), review_status=status,
                         qc_flags=';'.join(flags), diameter_method='2x_distance_transform_approximation',
                         excluded_region=bool(overlap), exclusion_overlap_px=overlap))
        objects.append((region.label, m['skeleton'], region.centroid, status == 'accepted', (y0, x0)))
        # Retain existing geometry for display; never recover centerlines from
        # a rendered image, where text could obscure or resemble skeleton pixels.
        centerlines[int(region.label)] = np.argwhere(m['skeleton']).astype(np.int32) + np.asarray((y0,x0),np.int32)
    parameters = {
        'segmentation': segmentation_parameters(segmenter),
        'sigma_px': float(sigma), 'min_area_px': int(min_area), 'endpoint_exclusion_px': float(exclusion_px),
        'exclusion_regions_xyxy': [list(r) for r in regions_to_exclude],
        'exclusion_policy': 'intersecting candidates retained and flagged; excluded from accepted statistics',
    }
    return PixelAnalysis(image, mask, labels, annotate(image, objects, regions_to_exclude),
                         pd.DataFrame(rows, columns=PIXEL_COLUMNS), parameters, centerlines)


def calibrate_analysis(pixels, calibration, render=True):
    """Convert distances only; do not rerun segmentation or alter pixel geometry."""
    if calibration is None or not np.isfinite(calibration) or calibration <= 0:
        raise ValueError('Enter a verified, finite, positive calibration in µm/pixel.')
    table = pixels.table.copy()
    for name in DISTANCE_FIELDS:
        table[name + '_um'] = table.pop(name + '_px') * calibration
    table['calibration_um_per_px'] = float(calibration)
    table = table[COLUMNS]
    accepted = table[counted_candidates(table)]
    summary = {'candidate_count': len(table), 'accepted_isolated_count': len(accepted),
               'review_required_count': len(table) - len(accepted),
               'excluded_region_count': int(table.excluded_region.sum()),
               'accepted_mean_length_um': float(accepted.length_um.mean()),
               'accepted_mean_diameter_um': float(accepted.mean_diameter_um.mean())}
    parameters = {**pixels.parameters, 'calibration_um_per_px': float(calibration)}
    id_labels = fiber_id_layout(pixels.labels, table) if render else None
    annotated = composite_id_layer(pixels.annotated, fiber_id_layer(pixels.labels, id_labels)) if render else None
    return Analysis(pixels.original, pixels.mask, pixels.labels, annotated, table, summary, parameters,
                    centerlines=getattr(pixels,'centerlines',{}), id_labels=id_labels)


def ensure_annotation(result):
    """Materialize detailed display pixels only when shown or exported."""
    if result.id_labels is None:
        result.id_labels = fiber_id_layout(result.labels, result.table)
    if result.annotated is None:
        from .visualization import centerline_annotation
        image = centerline_annotation(result.original, result.table, result.centerlines,
                                      result.parameters.get('exclusion_regions_xyxy', ()))
        result.annotated = composite_id_layer(image, fiber_id_layer(result.labels, result.id_labels))
    return result.annotated


def analyze(image, calibration, image_id="image", segmenter=None, sigma=0.0, min_area=20,
            exclusion_px=2.0, exclusion_regions=()):
    if calibration is None or not np.isfinite(calibration) or calibration <= 0:
        raise ValueError('Enter a verified, finite, positive calibration in µm/pixel.')
    return calibrate_analysis(analyze_pixels(image, image_id, segmenter, sigma, min_area,
                                           exclusion_px, exclusion_regions), calibration)
