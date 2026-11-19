# Independent scientific validation

The validation tools separate biological identity, detection, boundary quality,
operational approval and reliable measurement. No real-image accuracy improvement
has been established. Start with the [repository/data audit](scientific-validation-audit.md)
and [technical baseline findings](scientific-validation-baseline.md).

## Existing processing and experimental changes

Decoding retains the original image plane and coordinates. Normalization and
thresholding produce connected components; weighted skeleton paths provide length,
and twice the distance transform at interior centerline pixels approximates mean
diameter. Branches, cycles and unavailable geometry remain flagged. Neither green
nor red establishes biological truth. Green includes explicit human approval; a
green object with unreliable geometry can still be absent from measurement statistics.
Dark blue is the temporary active review target.

Original Otsu/manual/local segmentation remains available. The new, opt-in
background-illumination sigma subtracts a reflected Gaussian background without
resizing. Hysteresis retains weak signal only where connected to strong seeds.
Both can amplify debris or join structures; they are experimental settings, not
validated improvements. Default sigma is zero and the existing default parameter
identity is preserved so saved reviews continue matching unchanged analyses.

Automatic separation of invisible crossing boundaries has not been introduced.
Use the existing operational outline repair for independently visible fibers,
including missed objects. Border-truncated candidates remain available for review;
uncertain measurements are not fabricated. Changing segmentation can change
component IDs: compare methods by spatial reference matching, not by numeric ID.

## Calibration belongs to each original

Record verified **µm/pixel**, its source, and magnification separately for every
original. Calculate calibration as physical bar length divided by its measured
pixel span in that exact image. A changed magnification, resized acquisition or
export can change the span. The same “20 µm” text does not prove equal µm/pixel.
For the confirmed Sample 3 example, 20 / 344.0015 is approximately
0.05813928 µm/pixel; this is not a default for the other images.

The analysis entry and independent reference calibration must agree for a physical
comparison. Missing calibration produces pixel-unit inventories, not invented
µm results. Confirm analytical exclusions for scale-bar/text before benchmarking.

## Annotating in the website

1. Upload the unmarked original, enter its calibration and Analyze. Keep the
   existing original/analysis comparison and operational corrections as needed.
2. Open **Scientific validation and independent reference annotations**. Enter
   reviewer, verified biological specimen and acquisition group IDs. Leave unknown
   identities blank; do not substitute each filename as an independent specimen.
   Record calibration provenance and magnification.
3. Choose one category: `valid_measurable_fiber`,
   `real_fiber_not_measurable`, `non_fiber`, or `ambiguous`. Optionally enter paired
   manual length and/or mean diameter in µm and the reference measurement source.
   Missing manual values remain missing; algorithmic values are never copied into
   reference measurements automatically.
4. **Save selected detection category (not boundary truth)** links a category to
   the selected candidate and preserves its immutable original prediction. Its
   copied mask is marked `prediction_reference`; it cannot establish detection
   or segmentation ground truth.
5. For independent boundaries, configure the form, then choose **Independent
   reference outline** above the main viewer. That mode shows original pixels
   without decision overlays. Draw a polygon around a visible object, then save
   the reference boundary. Zoom/pan remain available; A/R and operational Undo/Redo
   are disabled in this mode. Ctrl+Z removes the last vertex. Return to Review to
   restore decision colors. This tool can annotate a fiber the algorithm missed.
6. Select an existing reference ID to update its category/measurements or replace
   its independent boundary. IDs remain stable through those edits. Operational
   acceptance, candidate masks, measurements and correction history are unaffected.
7. Mark exhaustive coverage only after reviewing **every** fiber/background object
   in the stated original-coordinate rectangle. Explicitly mark ambiguous regions.
   Fifty selected ImageJ fibers are partial coverage, not an exhaustive image mask.
8. Download versioned reference JSON. Reimport only against the exact original
   pixels. Older versions can be restored as a new revision. Reopening the same
   image restores saved references. Reload saved annotations after a stale-version
   warning before editing further.

A reference record contains schema/version/parent/revision, source pixel identity,
shape/dtype, reviewer, specimen/acquisition, split/development exposure, calibration,
coverage rectangle, independent UUID object IDs, categories, compressed cropped
masks with original `bbox_yxyx`, manual measurements and immutable prediction links.
Polygon coordinates are original XY pixels. Immutable versions live additively in
`reference_versions` in the existing feedback SQLite database. The existing
consistent database backup includes them. Keep JSON and backups outside the code
repository. Classification-only examples can later be replaced with independently
drawn boundaries without deleting the original prediction link.

## Specimens, acquisition groups and final tests

Splits use connected groups: sharing **either** a specimen ID **or** acquisition
ID keeps images together, including transitive links through another image. All
aliases, masks and crops of an original belong to its group. Random crops are not
independent test data. Missing group metadata blocks scientific scoring/promotion.

Split names are `unassigned`, `development`, `validation`, `test`. Seed 42 makes a
proposal reproducible; it does not make the data representative. At least three
independent connected groups are needed for an automatic three-way proposal.
Previously tuned/developed images, including the currently inspected originals,
cannot retrospectively become a blind final test. The proposal reserves final
test groups only from images explicitly confirmed never used for development.
Do not change this flag simply to make the split command pass.

Choose thresholds on development data, select settings on validation, and freeze
profiles and model versions before final test evaluation. Do not repeatedly tune
against final test results. Agree on clinically/scientifically acceptable length
and diameter errors before interpreting a result as sufficient.

## Offline validation commands (PowerShell)

Open PowerShell in the extracted project folder containing `app.py`. Use the
Python version/environment already installed for the application. These examples
use the experimental 3.15 environment; the stable `.venv` uses the stable core lock.
Optional plotting packages are separately pinned and do not change core locks.

```powershell
& ".\.venv315\Scripts\python.exe" -m pip install -r requirements-validation.lock.txt -e ".[validation]"
& ".\.venv315\Scripts\python.exe" -m collagen.scientific_validation prepare --images "C:\CollagenData\Originals" --output "C:\CollagenData\DraftDataset"
```

Preparation writes a draft `dataset.json`, references and `profiles.json`, without
inventing labels, calibration or specimen IDs. The manifest lists `{image,
reference}` file paths; paths resolve relative to the manifest. Replace each draft
reference with the corresponding exported, reviewed JSON. Keep source images at
original resolution. Update verified grouping/splits before a scientific run.
For a new split proposal, complete metadata first:

```powershell
& ".\.venv315\Scripts\python.exe" -m collagen.scientific_validation split --manifest "C:\CollagenData\DraftDataset\dataset.json" --output "C:\CollagenData\FrozenSplit" --seed 42
& ".\.venv315\Scripts\python.exe" -m collagen.scientific_validation run --manifest "C:\CollagenData\FrozenSplit\dataset.json" --profiles "C:\CollagenData\DraftDataset\profiles.json" --split test --match-iou 0.5 --output "C:\CollagenData\Reports\TestRun01"
```

For an explicitly non-scientific technical inventory before annotations/groups:

```powershell
& ".\.venv315\Scripts\python.exe" -m collagen.scientific_validation run --manifest "C:\CollagenData\DraftDataset\dataset.json" --split unassigned --output "C:\CollagenData\Reports\Inventory01"
```

Use a fresh output directory for every report. Existing report/manifests are not
overwritten. `--no-plots` explicitly skips the optional plots. The supplied
`profiles.json` compares original dark Otsu with the same pipeline plus experimental
background sigma 25 pixels. Set polarity, smoothing and analytical exclusions for
the acquisition on development data before freezing a benchmark. All methods must
share measurement/QC definitions and exclusions; profile differences are recorded.

## Metrics and report interpretation

Open `report.html` locally; images and CSVs sit alongside it. `report.json` records
source hashes, pixel identities, annotation versions, code commit/source hashes,
runtime packages, parameters, seed, split and readiness. Original-coordinate label
TIFFs and prediction CSVs preserve IDs and bounding boxes. Matching/error CSVs
include image/specimen/reference provenance; reference snapshots are retained.

- Instance matching is one-to-one, maximizing the number of matches above the
  declared IoU threshold, then total IoU. A biological true positive is a matched
  measurable **or** real-unmeasurable fiber. Overlap below threshold is reported
  separately from no overlap; overlap counts help inspect merges/splits.
- Independently drawn masks only define boundary/detection truth. Copied prediction
  masks are excluded; a copied mask inside a reviewed scope prevents treating that
  scope as fully independently annotated.
- In partial annotations, unmatched predictions are unknown. Recall applies only
  to annotated fibers; global precision, F1, new-FP count and pixel IoU remain
  unavailable. Exhaustive rectangles allow scoped FP and IoU calculations.
  Ambiguous reference regions and explicit analytical exclusions are ignored in
  scoring without altering original predictions or geometric measurements.
- Confusion matrices compare the four human categories to the actual QC outcomes
  (`measurement_included`, `review_required`, `not_detected`). The existing pipeline
  does not output a four-way biological class, so no four-way classifier is invented.
  Conditional measurability precision/recall/F1/FPR/FNR use defined reference
  objects. Detection FPR is unavailable because a population of background true-
  negative objects is not defined; detection precision/F1 are reported instead.
- Measurement pairs require matched valid references and positive finite manual
  values. Reports separate all valid matched references from operationally counted
  ones, and retain missing geometry. Per-pair signed/absolute/relative errors, bias,
  MAE, median absolute error and Bland–Altman plots are provided. Limits use sample
  SD and require at least two pairs. No pairing means unavailable, not zero error.
- Recovery compares matched reference IDs, not component-number changes. Lost true
  fibers and new spatial FP regions are also reported. Candidate-count increases
  alone cannot demonstrate improvement. Same-image plots show reference failures,
  recovered/lost outlines and distributions only where independent labels exist.
- Pooled object/pixel/pair metrics are descriptive; per-specimen detection counts
  are also exported. Fibers within an image are correlated. No independent-specimen
  confidence interval, biological treatment effect or acceptance tolerance is implied.

## Existing feedback learning and inference

No new real-data model was trained in this task. Independent references support
future development; saving a boundary does not automatically train a segmenter.
The existing CPU logistic object classifier remains experimental and cannot find
an object absent from its candidate mask. It can learn from operational human
accepted/rejected examples, including a manually added operational outline, once
baseline validation and explicit reviewed labels justify training.

Training now requires verified metadata for at least two independent training
**groups** and two separate promotion-evaluation groups, each role with at least
five accepted and five rejected examples and two images. Transitive overlaps block
promotion. Final-test reference labels are excluded from training/promotion samples;
validation-split labels cannot be used for fitting. The existing promotion rule
(≥1 percentage-point balanced-accuracy gain versus baseline and active model, with
no worse acceptance precision) and rollback/version history remain. Metadata or
correction changes during fitting invalidate promotion. This agreement gate does
not establish scientific generalization.

Use **Retrain Model Using Corrections** only when those conditions and baseline
validation are met. Approved inference remains automatic on subsequent analyses;
human overrides retain priority, geometry/calibration stay unchanged. Download the
active numeric JSON checkpoint in the feedback panel. Scientific comparison of a
checkpoint requires frozen specimen/acquisition provenance and a final evaluation
group unseen during both fitting and promotion. Older provenance-free checkpoints
still support existing operational inference/restore; they cannot pass this audit.

To evaluate an eligible frozen checkpoint, add a profile to `profiles.json` using
the same segmentation/QC settings and `"classifier_checkpoint": "model.json"`.
The path resolves relative to that profiles file. Do not unpickle external models.
No U-Net, pretrained microscopy model, GPU requirement or segmentation training
was introduced; insufficient independent data currently prevents demonstrating
ML benefit.

## Developer verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python desktop.py --smoke-test
python tools/check_feedback_browser.py --python .venv/bin/python --network-latency-ms 250
python tools/check_scientific_browser.py --python .venv/bin/python --network-latency-ms 250
```

Browser checks require the optional browser extra and Chromium. They use isolated
synthetic data/database, not user reviews. Scientific tests exercise exact source
identity, independent versus copied masks, partial coverage, assignment, missing
measurements, group leakage, versioning, exports, frozen inference and plots.
Windows workflows also run the reference browser test against the packaged app;
optional report dependencies have their own Linux/Windows CI jobs. Passing these
software checks is not evidence of real-image accuracy.
