# Collagen Fiber Analyzer

A modular research application for single-plane TIFF, PNG, and JPEG microscopy images, with conventional segmentation, persistent manual corrections, and optional experimental feedback-trained object classification. Scientific accuracy is **not established** until validated against representative real images and independently measured ground truth.

## Run locally

Use Python 3.12 for the stable locked setup. An isolated, experimental
[Python 3.15.2 setup](docs/python315.md) uses a separate package list and CI
workflow; it includes a prerelease scikit-image dependency. From this repository:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt -e '.[dev]'
python -m pytest
python -m streamlit run app.py
```

Enter the image's calibration in micrometers per pixel. Calibration must be isotropic; resample anisotropic acquisitions with appropriate calibration before analysis. No automatic inference from image metadata is performed. Uploaded pixels and exported originals retain their dimensions and bit depth. TIFF stacks are rejected rather than silently selecting a plane.

Calibration starts blank for each original image. Enter the verified value, review
the scale-bar/text region if proposed, and press **Analyze image**. The default
polarity is dark fibers on a lighter background. Parameter edits take effect on
submission; existing results always reflect the last submitted settings.

The lower-right scale-bar proposal is a heuristic preview, not a calibration
estimate. If a panel is proposed, explicitly choose **Exclude rectangle** (adjust
its original-pixel coordinates if needed), or **No exclusion needed (reviewed)**.
For other annotations, enter and confirm a custom rectangle. Every intersecting
candidate remains in the mask, labeled output and CSV with `excluded_region`, its
overlap pixel count and `review_required` status. Whole candidates are flagged;
their centerlines are not cut into artificial fibers. Confirmed regions are
outlined on the annotated image and excluded from accepted summary/error statistics.

Pixel measurements are cached separately from calibration with a bounded
two-entry cache. Calibration changes reuse geometry. Ambiguous centerlines skip
width work that cannot produce a reportable measurement. The full QC archive is
built only after **Prepare QC archive**, then downloaded using its download button.
Archives include `analysis_parameters.json` with segmentation settings, exclusion
rectangles, calibration and (in the UI) input hash and operator review choice.
Empty/corrupt image inputs produce validation errors rather than decoder tracebacks.

## Workflow and interpretation

Choose bright or dark fibers, Otsu/manual/local thresholding, optional Gaussian smoothing, and optional opening/closing radii in pixels. Defaults disable morphology to avoid introducing merges. Inspect the mask for missed fibers and merged objects before interpreting measurements. Intensity normalization is global min/max and color images are converted to luminance; stain-specific color separation is not included.

Each 8-connected foreground component receives a deterministic ID in that analysis. IDs are not stable across parameter changes. Components are candidates, not assumed individual fibers. Only components with one open, unbranched centerline, sufficient area, no border contact, and usable width samples count as accepted isolated fibers. Branched crossings/merges, cycles, tiny objects, and border-truncated objects remain in the table with flags. A smooth end-to-end merge can be indistinguishable from a single fiber in a binary mask: automated flags cannot detect all merges. Review accepted objects too.

Skeleton edges use weights 1 for horizontal/vertical movement and sqrt(2) for diagonals. Redundant diagonal shortcuts across orthogonal corners are omitted. Accepted curved length is the sum of path edges multiplied by calibration. Ambiguous topology has missing length/width, rather than a misleading sum across branches. Border candidates may have partial measurements but are excluded from accepted statistics.

Width is **twice the Euclidean distance to background at centerline pixels**, an approximation to manual perpendicular edge-to-edge diameter. Endpoint/junction neighborhoods are excluded using the larger of the configured radius and local distance-transform radius. Mean and population SD describe retained width samples, not uncertainty or between-fiber variability. Digital boundaries, skeleton bias, focus, stain, threshold choice, and curvature affect estimates. Length is between skeleton endpoints and does not include inferred end-cap extensions. Digital arc lengths have orientation-dependent bias.

The results show **Original Microscope Image** on the left and **AI Fiber Analysis**
on the right, with matching scale and aspect ratio. Narrow screens stack the views.
Zoom and scrolling are shared so the same region stays aligned; only the analysis
view accepts review clicks. The original preview retains decoded uint8/uint16 pixels
without annotations, resizing or contrast changes. Other TIFF pixel types require
a labeled browser display conversion; the original analysis/download pixels remain intact.

The [Clean Research Lab interface](docs/research-lab-ui.md) groups settings and review
tools, displays the original project graphic, and shows existing saved summary
counts above the workspace. Raw summary data and detailed QC remain expandable.
The presentation does not change scientific processing or saved review decisions.

The analysis view is an interactive, full-resolution overlay on the original:
**green** is a #00FF00 tint over approved masks and **red** marks rejected or
unapproved masks. Explicit saved human approval controls the highlight even when
geometry or selection filters exclude the object from numerical measurements.
The **Measured fibers** count and black ID callouts still require eligible
measurements; green alone is not a measurement-validity claim. The CSV separates
`review_approved` from `included_in_measurement_statistics`.
Left-click to accept, right-click to reject; choose **Left click toggles** for the
earlier behavior. A/R accept/reject the selected object; N advances through
unreviewed objects. Undo/Redo and Ctrl+Z/Ctrl+Y preserve history. Enable/disable
keyboard shortcuts in the editor. Background clicks never select nearby objects.
The selected ID and decision appear above the images, and the Accept/Reject
buttons name their target ID. A temporary dashed box marks that object's bounds
without changing exported images. Clicking background clears the toolbar target.
See [approval highlights and click diagnostics](docs/approval-highlights.md) for
the saved-state policy, measurement distinction and optional event trace.

Under **Fiber selection and measurement eligibility**, configure optional diameter
range (150–335 nm initially, **disabled by default**), minimum length in µm, area in
µm², mean width in pixels, border exclusion, and crossing review/exclusion.
The three-pixel default mean-width screen is configurable and does not establish
optical resolution. All detections, raw measurements and QC flags remain available.
See [selection, review controls and performance](docs/fiber-selection.md) and
[manual review and learning](docs/feedback-learning.md).

Color changes are local and provisional until acknowledged by the saved review.
The editor queues rapid corrections and restores confirmed decisions on a failed
save. Wait for **All corrections saved** before exporting, switching images or
closing. Changing filters never reruns segmentation or centerline measurement.
Each saved correction refreshes its object's color and ID visibility, including
when the immediate provisional paint was missed. Approval cannot bypass
measurement eligibility; an ineligible approved mask stays green with its reason
and **NOT counted** status shown. The **Review mode / Outline mode** indicator
always identifies whether a left-click approves a mask or adds an outline vertex.
For an approved label whose mask has missing or branched geometry, the editor
shows **NOT counted**, the available diagnostic details, and **Outline selected
fiber**. Trace one separated boundary and save it to obtain measurements through
the existing method. The outlined object gets a new ID; the original detection
and its missing measurements remain in the audit dataset. Review mode resumes
after saving this selected-fiber repair.

Plain black ID labels with no background label **only measurable accepted fibers**, using the
exact `fiber_id` exported in the Excel-readable CSV. Labels prefer background beside
each fiber, with short leaders anchored to that same object. Zoom for crowded areas;
the editor's **Show accepted fiber IDs** checkbox can hide labels for boundary
inspection without changing decisions or data. Rejected, excluded and accepted-but-
unmeasurable objects have no ID callout. The detailed annotation follows the same rule.
Text is sized in original image pixels; use zoom to read IDs on large microscopy
images. Crowded regions can require longer leader lines or still have overlap.
The default glyph height is 14 pixels, reduced from the earlier 16-pixel style.

The four processing stages—original, binary mask, labeled candidates, and detailed
IDs/centerlines—remain in the collapsed **View Detailed Fiber Analysis** section.
They render when opened; opening/closing it reuses cached analysis. Detailed
centerlines also use green/red. The scale-bar preview is in the sidebar's exclusion
controls. This tracked expander requires Streamlit 1.65 or newer (as pinned).

Download CSV or a QC ZIP with original, segmentation, labels, detailed annotation
and `count_overlay.tiff`, plus CSV, summary and analysis parameters.
`review_overlay.tiff` matches the primary approval view; `count_overlay.tiff`
retains the measurement-inclusion view. Both retain measured-fiber ID labels; `fiber_id_labels.json`
records each displayed ID, its source image, anchor and placement. Original images,
instance masks, measurements and CSV fields are unchanged by labeling.
Endpoints are JSON lists of `[x, y]` pixel coordinates. Missing measurements are
empty CSV fields. The full CSV preserves original decisions, QC flags, manual/model
provenance and measurement eligibility. A separate accepted-measurements CSV contains
only measurable accepted fibers. A green counting decision does not
establish biological correctness or measurement accuracy.

## Modules

- `loading`: format decoding and single-plane validation
- `preprocessing`: full-resolution luminance normalization and optional smoothing
- `segmentation`: configurable conventional thresholding and `Segmenter` protocol
- `detection`: connected candidate labeling
- `geometry`: skeleton graph, physical length, approximate width
- `quality`: acceptance and exclusion flags
- `exclusions`: validated original-pixel rectangles and scale-panel proposals
- `visualization`: IDs and centerlines
- `id_labels`: display-only accepted ID selection, callout placement and compositing
- `export`: CSV and complete QC archive
- `validation`: explicit ground-truth matching
- `pipeline`: orchestration and accepted-only statistics
- `feedback`: local SQLite masks, correction/Undo history, training membership and model registry
- `review`: manual and model decisions over immutable baseline masks and geometry
- `learning`: experimental object classifier and image-held-out promotion checks
- `editor`, `review_ui`: offline clickable canvas and review/training controls

A future U-Net adapter can implement `Segmenter.__call__`, receiving a full-resolution normalized image and returning a same-size boolean mask. Geometry, QC, and export remain independent. Feedback learning currently trains an object classifier only; segmentation model training is not included.

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
It starts with the conventional baseline. No pretrained model is bundled; a local
object classifier can be trained from explicitly reviewed corrections.

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

### Application icon

The executable, launcher window and browser tab use the collagen artwork in
`assets/`. The original PNG is retained byte-for-byte as `collagen-original.png`.
Runtime assets are a 256-pixel PNG and an ICO with 16, 24, 32, 48, 64, 128 and
256-pixel frames. Regenerate them with:

```bash
python tools/build_icon.py artwork.png --output-dir assets
```

Build the Windows package afterward to embed the ICO. The packaged smoke test
checks that the assets exist and Windows can load the window icon. To create a
desktop shortcut, right-click the extracted executable, choose **Show more
options > Send to > Desktop (create shortcut)**. The shortcut uses the embedded
application icon.

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
