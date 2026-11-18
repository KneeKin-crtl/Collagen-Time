# Calibrated fiber selection and fast review

Detection, measurement and eligibility are separate. Threshold segmentation and
the existing centerline/distance-transform methods still run at original resolution.
Selection evaluates retained measurements; it never deletes an instance or changes
its length, diameter, endpoints or mask. Filtering and calibration changes preserve
the existing review identity and fiber IDs. Changing segmentation/QC parameters can
create different instances, as before.

## Configure selection

Open **Fiber selection and measurement eligibility** in the analysis controls,
then select **Analyze image** to apply the submitted settings.

| Setting | Initial value | Meaning |
| --- | --- | --- |
| Research diameter range | Disabled; 150–335 nm when enabled | Inclusive range for this research question, not a definition of a valid fiber |
| Minimum length | 0 µm | Screen measured curved length |
| Minimum component area | 0 µm² | `area_px × calibration²`; independent of diameter |
| Minimum reliable mean width | 3 pixels | `mean_diameter_um / calibration_um_per_px`; configurable sampling screen |
| Border exclusion | Enabled | Exclude truncated objects from measurements |
| Crossing/merge policy | Require review | Or exclude automatically while retaining the object and QC flags |

The earlier **Minimum accepted area (pixels)** baseline QC setting remains separate
and is recorded in analysis parameters. Neither area setting is a fiber diameter.
Zero disables a minimum size screen; the research range has its own enable switch.
All physical conversions use the entered, verified isotropic calibration.

Three pixels is a configurable mean-width heuristic, not a proven resolution limit.
The minimum is applied to the mean of retained local width samples, not the thinnest
point along a fiber. The UI warns when part of the selected research range falls
below that screen. Even finely sampled images may be limited by optical resolution,
focus or segmentation. This application cannot establish that 150–335 nm fibers are
resolvable from calibration alone. Distance-transform diameter remains an approximate
manual perpendicular edge-to-edge width; real-image validation is still required.

## Categories and overrides

| Category | Meaning |
| --- | --- |
| Accepted | Selected measurable fiber; included in measurements |
| Rejected | Explicit human rejection; retained and red |
| Out of Range | Measurable candidate outside the enabled research diameter range |
| Too Small to Measure | Fails a configured length, area or mean-width screen |
| Ambiguous | Missing/uncertain geometry, a crossing/merge, an excluded region, or a border exclusion |
| Noise | Existing classifier's non-fiber suggestion; explicitly flagged as unvalidated |

The conventional baseline cannot reliably distinguish every artifact from every
real fiber. Uncertain detections remain ambiguous; smooth fused fibers may escape
automatic flags. Review accepted objects too. No new segmentation model, training
method or confidence score is introduced by this feature.

A human acceptance can override a research-range or classifier suggestion. It does
not bypass configured minimum-size/reliability gates, excluded regions or missing
geometry. Such a label is saved as `manual_classification=accepted`, but the object
is green as a review approval, but excluded from measurements with its reason. Adjust the threshold or accurately outline
a separated fiber if appropriate. Manual rejection is always retained distinctly
from an automatic exclusion. Filtering does not create training examples.

Border exclusion can be disabled explicitly. This permits finite measurements of
the **visible partial fiber**, retains the border QC flag, and reports the number
of partial lengths included. It never extrapolates a missing end. Crossing/merged
objects without usable geometry remain unmeasurable in either crossing policy;
“exclude” removes them from the review-only view, not the audit dataset.

## Review controls and saved decisions

- Left-click accepts and right-click rejects the exact detected object. Select
  **Left click toggles** for the earlier behavior; background never snaps to a neighbor.
- A/R accept/reject the selected object. N/Right Arrow advances through unreviewed
  objects in the current view; P/Left Arrow goes back. Click the image to give it
  keyboard focus, or disable shortcuts with the checkbox.
- Undo/Ctrl+Z and Redo/Ctrl+Y (also Ctrl+Shift+Z) preserve append-only history and
  restore the same IDs and masks. A fresh correction invalidates the redo branch.
- **Hide automatic exclusions** and **Only requiring manual review** change only
  visibility. Full CSV and QC exports still retain those detections.
- Progress reports reviewed/total detections, detections shown and selected measurable
  fibers. Only eligible measured fibers have numeric callouts matching exported `fiber_id` values.
  Green review approval is exported separately as `review_approved` and does not
  fill missing measurements or change eligibility.

Colors/counts update locally while saves are queued. Pending colors/counts are
clearly provisional; wait for **All corrections saved** before exporting, changing
images/settings or closing. A failed/stale save restores confirmed decisions and
cancels dependent queued previews with a visible error. The browser warns before
closing a tab with pending saves. Queues are bounded at 128 pending actions; further
input is refused with a message until saves catch up.

Only accepted/rejected requests enter saved corrections and the existing training
workflow. Do not manually label a real fiber as a non-fiber solely because its
diameter is outside a research range; use the range filter for that selection.

## Exports and persistence

Existing per-fiber fields are retained. Additional fields include:

- `selection_category`, `automatic_category`, `automatic_exclusion_reason`,
  `exclusion_reason`, `selection_override`, `requires_manual_review`;
- `area_um2`, `mean_width_px`, `mean_diameter_nm`, `diameter_sd_nm`;
- `quality_flags` and `filter_thresholds` (JSON), alongside existing QC flags and
  `included_in_measurement_statistics`.

`automatic_classification` retains the original binary rules/model suggestion;
`automatic_category` adds selection filters before the manual decision. The final
category determines website measurement inclusion. An override retains its original
automatic exclusion reason. No numerical “confidence” is invented from a width screen.

The QC ZIP includes applied selection settings, full CSV, original image, unchanged
segmentation/baseline labels, reviewed labels, annotated TIFFs, ID mapping and manual
masks. Saved correction history also records the active filter/calibration context.
Old SQLite reviews migrate additively; existing masks, models and events are retained.
Reopened images restore corrections, while selection settings must be reapplied as
appropriate; exported parameters provide the reproducible settings record.

## Why review is faster

Previously each click waited for full-image normalization, annotation/ID layout,
PNG encoding and browser reloads across Streamlit reruns. Geometry was already cached,
but display reconstruction still delayed visible feedback.

The editor now keeps immutable source/identity layers in a bounded browser cache,
projects one object's pending decision, and redraws its bounding region and ID
sprite. Server messages update decision records and missing sprites; acknowledged
image layers are referenced instead of repeatedly transmitted. A cache miss requests
the original layers again. Source, calibrated baseline and review measurements are
reused; detailed annotations render only when opened/exported. Only a newly drawn
outline requires new geometry.

Benchmark both visual feedback and save acknowledgment separately:

```bash
python -m pip install -e '.[browser]'
python tools/benchmark_review.py --python /path/to/environment/python --output-dir review-benchmark
```

Use installed Chrome on Windows or pass `--browser-executable` for a local Chromium.
The tool generates a 4096 × 3072 image with 400 isolated objects, uses an isolated
temporary feedback database, and records eight click samples to `latency.json`.
Visual latency runs from `pointerup` to the animation frame after the changed object
pixel is observed. Save acknowledgment is a separate metric. No timing assertions
make normal tests dependent on machine speed. See the recorded before/after report
in [review-speed-benchmark.md](review-speed-benchmark.md).
