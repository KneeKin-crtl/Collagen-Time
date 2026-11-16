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

Measure centerlines using segmented/freehand line selections with calibrated ImageJ units; measure perpendicular widths at multiple interior positions. Create a CSV with `image_id,fiber_id,length_um,mean_diameter_um`. Match candidate IDs explicitly from the annotated image; do not rely on row order. Use the uploaded filename as `image_id`, and leave unavailable measurements blank. Duplicate or missing IDs and nonpositive, infinite, or nonnumeric measurements are rejected.

Upload this CSV in the UI or call `collagen.validation.comparison_report(analysis.table, manual_table)`. The automated table must include `review_status` (`accepted` or `review_required`); the manual table does not need this field. The report provides:

- Counts of all matched IDs, unmatched automated IDs, unmatched manual IDs, and accepted versus review-required matches. Matching uses both image and fiber IDs. These counts describe comparison coverage; manual data may be a subset, so unmatched IDs do not automatically mean detection failures.
- Per-candidate measurements and acceptance status, preserving every unmatched and excluded candidate. Signed errors (`automated − manual`, in µm) and relative errors are populated only for accepted matched fibers with both values available for that metric.
- Separate length and diameter statistics: paired sample count, accepted matches missing either measurement, bias (mean signed error), and mean absolute error (MAE). A missing length does not exclude an otherwise usable diameter pair. With no usable pairs, bias and MAE are missing, not zero.

Download the full comparison, error statistics, and match counts as three CSVs. `compare_ground_truth` remains the row-level API and applies the same accepted-only error rules. Neither API modifies its inputs.

For real-image validation, also assess segmentation errors, dispersion, and agreement across representative images. Acceptance is an automated QC classification, not proof of correct segmentation. This comparison mechanism is not itself evidence of scientific validation.

Synthetic tests separate geometry from thresholding, exercise straight/diagonal/curved fibers, diameter tolerances, ambiguous topology, border QC, formats, exports, and manual matching. Raster tolerances are explicit. Real-image validation remains future work.

The lock file captures the tested Python 3.12 environment. `pyproject.toml` declares supported dependency ranges; other Python/platform combinations may need a separately validated lock.

## Windows desktop build

The desktop launcher opens the local application in your default browser and
stops its server when you close the launcher. The intended Windows 10/11 x64
bundle includes Python and dependencies and requires no network for analysis.
It contains the conventional baseline, not a trained AI model.

In GitHub, open **Actions > Build Windows desktop**, choose the run for the desired
branch, and download **CollagenFiberAnalyzer-Windows-x64** from its artifacts after
all checks succeed. Extract the whole ZIP and double-click
`CollagenFiberAnalyzer.exe`; retain its `_internal` folder. An artifact is uploaded
only after tests and a packaged analysis/server smoke check pass. Builds require a
Windows runner; a Linux build is not a Windows executable. The launcher/browser
interaction still needs a check on the target Windows workstation.

For a developer build on Windows, from the repository root:

```powershell
python -m pip install -r requirements.lock.txt -r packaging/windows/build-requirements.txt -e ".[dev]"
python -m pytest -q
python desktop.py --smoke-test
python -m PyInstaller --noconfirm packaging/windows/analyzer.spec
```

For a source launch: `python desktop.py`. For cloud/headless use, retain the
Streamlit launch command above; the desktop control window requires a graphical
session and Tk. Temporary startup logs live in `CollagenFiberAnalyzer` under the
Windows temporary directory. The Windows bundle is currently unsigned.

## Bowlin Excel workbook review

The app can import the condition worksheets in the supplied workbook layout,
independently of an image upload. It extracts only rows with numbered per-image
ROIs and ignores pooled copies. Length and diameter are kept as separate records,
with worksheet/cell provenance. Values retain original workbook units; units,
diameter sampling and image/fiber correspondence must be confirmed before paired
scientific comparison. Download normalized manual measurements and an image
mapping template from **Review ImageJ Excel workbook**.

See [dataset preparation and validation](docs/dataset-and-validation.md) for
calibration, ROI exports, ambiguous objects and future AI requirements. User
images, measurements and model weights are not included in the repository or
Windows build.
