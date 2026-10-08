# Collagen Fiber Analyzer

A modular, non-AI research baseline for single-plane TIFF, PNG, and JPEG microscopy images. Scientific accuracy is **not established** until validated against representative real images and independently measured ground truth.

## Run locally

Requires Python 3.11 or newer (tested with Python 3.12). From this repository:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt -e '.[dev]'
python -m pytest
python -m streamlit run app.py
```

Enter the image's calibration in micrometers per pixel. Calibration must be isotropic; resample anisotropic acquisitions with appropriate calibration before analysis. No automatic inference from image metadata is performed. Uploaded pixels and exported originals retain their dimensions and bit depth. TIFF stacks are rejected rather than silently selecting a plane.

## Workflow and interpretation

Choose bright or dark fibers, Otsu/manual/local thresholding, optional Gaussian smoothing, and optional opening/closing radii in pixels. Defaults disable morphology to avoid introducing merges. Inspect the mask for missed fibers and merged objects before interpreting measurements. Intensity normalization is global min/max and color images are converted to luminance; stain-specific color separation is not included.

Each 8-connected foreground component receives a deterministic ID in that analysis. IDs are not stable across parameter changes. Components are candidates, not assumed individual fibers. Only components with one open, unbranched centerline, sufficient area, no border contact, and usable width samples count as accepted isolated fibers. Branched crossings/merges, cycles, tiny objects, and border-truncated objects remain in the table with flags. A smooth end-to-end merge can be indistinguishable from a single fiber in a binary mask: automated flags cannot detect all merges. Review accepted objects too.

Skeleton edges use weights 1 for horizontal/vertical movement and sqrt(2) for diagonals. Redundant diagonal shortcuts across orthogonal corners are omitted. Accepted curved length is the sum of path edges multiplied by calibration. Ambiguous topology has missing length/width, rather than a misleading sum across branches. Border candidates may have partial measurements but are excluded from accepted statistics.

Width is **twice the Euclidean distance to background at centerline pixels**, an approximation to manual perpendicular edge-to-edge diameter. Endpoint/junction neighborhoods are excluded using the larger of the configured radius and local distance-transform radius. Mean and population SD describe retained width samples, not uncertainty or between-fiber variability. Digital boundaries, skeleton bias, focus, stain, threshold choice, and curvature affect estimates. Length is between skeleton endpoints and does not include inferred end-cap extensions. Digital arc lengths have orientation-dependent bias.

The UI shows original, binary mask, labeled mask, IDs and centerlines (green accepted, orange review), summary counts, and the per-candidate table. Download CSV or a ZIP with lossless TIFF outputs, CSV, and JSON summary. Endpoints are JSON lists of `[x, y]` pixel coordinates. Missing measurements are empty CSV fields. QC flags and review status are automatic suggestions; no manual review editing is implemented yet.

## Modules

- `loading`: format decoding and single-plane validation
- `preprocessing`: full-resolution luminance normalization and optional smoothing
- `segmentation`: configurable conventional thresholding and `Segmenter` protocol
- `detection`: connected candidate labeling
- `geometry`: skeleton graph, physical length, approximate width
- `quality`: acceptance and exclusion flags
- `visualization`: IDs and centerlines
- `export`: CSV and complete QC archive
- `validation`: explicit ground-truth matching
- `pipeline`: orchestration and accepted-only statistics

A future U-Net adapter can implement `Segmenter.__call__`, receiving a full-resolution normalized image and returning a same-size boolean mask. Geometry, QC, and export remain independent. No model training is included.

## Validation with ImageJ

Measure centerlines using segmented/freehand line selections with calibrated ImageJ units; measure perpendicular widths at multiple interior positions. Create a CSV with `image_id,fiber_id,length_um,mean_diameter_um`. Match candidate IDs explicitly from the annotated image; do not rely on row order. Upload this CSV in the UI or call `collagen.validation.compare_ground_truth`. Results include signed absolute-unit and percentage errors plus unmatched IDs. Exclude ambiguous candidates from accuracy estimates using the analysis review status, and report sample counts, segmentation errors, length/diameter bias, dispersion, and agreement across representative images. This comparison mechanism is not itself evidence of scientific validation.

Synthetic tests separate geometry from thresholding, exercise straight/diagonal/curved fibers, diameter tolerances, ambiguous topology, border QC, formats, exports, and manual matching. Raster tolerances are explicit. Real-image validation remains future work.

The lock file captures the tested Python 3.12 environment. `pyproject.toml` declares supported dependency ranges; other Python/platform combinations may need a separately validated lock.
