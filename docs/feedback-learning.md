# Manual correction and experimental feedback learning

The original threshold segmentation, weighted centerline geometry, calibration,
and approximate distance-transform diameter methods are unchanged. A review layer
changes object acceptance and can add a human-drawn object. It does not pretend
that every connected component is one fiber.

## Review an image

1. Upload the original, enter its verified calibration, review scale-bar exclusion,
   and select **Analyze image**.
2. Before the first correction, choose **Training examples** or **Evaluation only**
   for the whole image. Keep at least two independent images for evaluation. This
   role locks after the first saved review and cannot be switched later.
3. Left-click a highlighted object to accept; right-click to reject. Select
   **Left click toggles** to use the earlier interaction. A/R act on the selected
   object, N advances to the next unreviewed object. Shortcuts require image focus
   and can be disabled. Only the label at the original-pixel click is selected.
4. Use **Undo** on the image, Ctrl+Z while the image has focus, or **Undo last
   correction** in the object-ID controls. Redo/Ctrl+Y restores the same action,
   mask and ID; a new correction clears the redo branch without deleting history.
   Both Undo and Redo survive reopening. Wait for **All corrections saved** before
   closing or exporting; rapid clicks are queued with provisional local colors.
5. Use **Review by object ID and save confirmations** to accept/reject a specific
   ID, including confirming an already correct automatic decision. Confirmations
   are important: corrections alone overrepresent difficult examples.

Clicking a merged connected component selects that component, not an inferred
individual fiber inside it. To isolate a fiber from a rejected merge, choose
**Outline missed / merged fiber**, click around its actual boundary and select
**Save outline**. Use Backspace to remove the last vertex or **Clear outline** to
start again. Outlines must be simple polygons within the image, without repeated
vertices. Existing accepted objects must be explicitly rejected before outlining
over them. Intersected originals stay rejected and auditable; only outlined pixels
change instance ownership in the review overlay. Undo removes the outline and
restores the original labels. No automatic splitting or edge guessing is done.

## Counts, measurements and exports

The primary view and detailed annotation show plain black `fiber_id` numbers,
without a background plate, at a default glyph height of 14 original pixels,
only for rows included in the measurement count. Match the printed number to the
`fiber_id` column of the CSV opened in Excel, not the spreadsheet row number.
Objects excluded from measurements have no callout. A manual acceptance that
fails measurement eligibility is highlighted green but retains its exclusion
reason and unavailable measurements.
The editor explicitly shows **approved (green); NOT counted** when this
happens. Missing length/diameter, reliable-sample availability and the number of
endpoints explain an ambiguous mask. **Outline selected fiber** focuses the
selected object and starts the existing outline tool. Save one correctly traced
boundary to create a new measured ID and return to review mode. The original ID,
mask, QC flags and correction history are retained; no missing measurement is
filled merely to turn an object green.
Callouts prefer nearby background and avoid each other when space permits; dense
areas may still overlap. Zoom or uncheck **Show accepted fiber IDs** to inspect
boundaries. This display toggle does not save a correction, change a measurement,
or remove IDs from downloaded annotated images.

The website uses [calibrated selection filters](fiber-selection.md): `accepted_count`
and `accepted_measurement_count` count selected measurable green fibers.
`accepted_isolated_count` excludes border-truncated partial fibers. Border objects
are excluded by default; explicitly disabling that filter permits flagged partial
lengths and reports `partial_length_included_count`. Crossings with missing geometry
cannot enter measurements. `manual_accepted_but_ineligible_count` identifies human
acceptances that remain excluded. A manual label cannot reconstruct a missing
centerline or endpoint. An accurate outline may resolve a merged mask, but its
quality depends on the operator. Direct Python callers without `SelectionFilters`
retain the earlier review/count policy for backward compatibility.

Existing measurements are reused when classifications change. Manual outlines use
the same geometry function and calibration. Full per-fiber CSV exports include
`classification`, `manual_classification`, `original_classification`,
`baseline_classification`, `automatic_classification`, `decision_source`, model
versions, object/analysis keys, original-pixel bounding boxes, review-event IDs,
`used_for_training`, `used_for_evaluation`, `measurement_status`,
`included_in_measurement_statistics` and overlay visibility. Rejected detections
and fully covered originals remain in the full CSV. Use **Download measurable
accepted fibers CSV** for the accepted measurement dataset.
The website also exports selection categories, automatic and final exclusion
reasons, calibrated area, mean width in pixels, diameter/SD in nm, and filter
thresholds. Research filters do not generate training labels: only explicit human
acceptance/rejection does. Use range filters for research selection rather than
labeling every real out-of-range fiber as a non-fiber training example.

The QC archive retains the original binary segmentation, adds the reviewed labels,
and preserves `baseline_labels.tiff`. Manual cropped masks and their original-pixel
bounding boxes are in `manual_masks.npz` (read with `numpy.load(..., allow_pickle=False)`).
Original images retain their resolution and bit depth. Calibration changes scale
measurements without changing masks or review identity.
The annotated TIFFs include the same ID layer as the primary view, and
`fiber_id_labels.json` records the ID/image/anchor mapping. Accept/reject, Undo,
calibration changes and reopening the same review never renumber unrelated objects.
Reprocessing identical pixels/settings preserves the mapping; different segmentation
or QC settings can produce different instances and IDs. Compare against the CSV from
that same analysis. Automatic IDs are not automatically ImageJ's manual ROI numbers.

## Persistent storage and backup

On Windows, data are saved under
`%LOCALAPPDATA%\CollagenFiberAnalyzer\feedback\feedback.sqlite3`, outside the
downloaded source or executable directory. Linux uses
`$XDG_DATA_HOME/CollagenFiberAnalyzer/feedback`, falling back to
`~/.local/share/CollagenFiberAnalyzer/feedback`. Set `COLLAGEN_FEEDBACK_DIR` to use
another writable location; for cloud development use a directory outside the
checkout, such as `/workspace/collagen-feedback`.

SQLite transactions persist each acknowledged correction. The database contains
original image pixels, exact cropped object masks, locations, initial automatic
decisions/model versions, append-only correction and Undo/Redo events, current labels,
and immutable training/evaluation membership per model version. No credentials,
network service, GPU, or third-party model download is needed.
An additive database migration preserves old reviews and adds the calibration,
selection settings and selection reason active when each new correction was made.
Undo/Redo retain that original context. Existing promotion thresholds are retained; verified specimen/acquisition group safeguards now apply (see below).

Re-upload the same original with the same segmentation/QC settings to restore
corrections. Identity uses decoded pixel content, shape, dtype and analysis
settings, not row order or filenames. Changing calibration preserves corrections;
changing segmentation/QC settings creates another analysis with new candidate
identities. Duplicate identical masks from one image are deduplicated for training.

In **Feedback learning, model evaluation and backups**, download the correction
history CSV or prepare a consistent SQLite database backup. Keep backups outside
the application directory; they contain the microscopy data. To restore a backup,
close all analyzer processes, preserve the current feedback directory, and point
`COLLAGEN_FEEDBACK_DIR` to a separate directory containing the backup named
`feedback.sqlite3`. Avoid copying a live database file without its WAL; use the
application's backup button.

## Retraining and using the improved model

Review at least **two independent training groups and two separate evaluation
groups**, with at least **two images and five accepted and five rejected examples
in each role**. Record verified specimen and acquisition IDs in the Scientific
validation panel; sharing either identity, including transitive links, blocks
role separation. Final-test labels are automatically excluded from fitting and
promotion evaluation; validation-split labels cannot enter fitting. Unknown group
identities block training rather than treating filenames as independent specimens. Select
**Retrain Model Using Corrections**. This trains a small CPU logistic classifier
using shape, intensity contrast, pixel geometry, and QC features. Only current
explicit human labels are targets; predictions are never added as training labels.
Each image contributes equally within each class to reduce domination by many
fragments from a single image. Scaling is fitted on training examples only.

The candidate, active model, and baseline are evaluated against the same held-out
specimen/acquisition-disjoint examples. The candidate is promoted only if balanced accuracy improves by
at least one percentage point over both comparators without lower accepted
precision. Reports include false acceptance/rejection counts, class recall,
precision, image IDs, sample counts and frozen group provenance. Changes to corrections during training
block promotion until a fresh run. Insufficient data or a failed comparison keeps
the existing model active. All candidate versions and exact labeled samples remain
stored, including failed candidates. Previously approved versions can be restored,
or **Use baseline classifier** returns to rule-based suggestions.

An approved model is used automatically on subsequent analyses, including after
restarting. Human overrides always take priority. Acceptance predictions never
change segmentation, skeleton paths, calibration or diameter algorithms.

This is **experimental classification agreement on a small, selected review
dataset**, not established scientific accuracy. Repeated evaluation on the same
images can itself overfit model selection; keep additional independent images for
final validation, including new samples/acquisition sessions. The classifier does
not find wholly missed fibers automatically. Manual outlines add those examples
and preserve masks for future segmentation work; no U-Net/segmentation training is
implemented. Assess missed detections, merges and measurement agreement separately
against representative real microscopy images and ImageJ ground truth.

## Developer browser check

Install the optional ".[browser]" extra and an available Chrome/Chromium browser.
Run "python tools/check_feedback_browser.py" for source, or add
"--executable dist/CollagenFiberAnalyzer/CollagenFiberAnalyzer.exe" for a frozen
build. "--browser-executable" can select a local Chromium binary. The check starts
its own isolated server/database and uses generated synthetic pixels, never the
operator's reviews. It exercises exact clicks, zoom mapping, outlines, CSV updates,
restoration in a new browser session, persistent Undo and training prerequisites.
Both native Windows build workflows run this check against the packaged executable
before uploading the application artifact. Playwright is a developer-only extra;
it and the test browser are not bundled as application dependencies.

## Independent reference annotations

The [scientific validation guide](scientific-validation.md) documents four-way
reference categories, independent/missed masks, versioned JSON and grouped reports.
Those references are separate from operational binary corrections. Copied prediction
masks are never treated as independent segmentation truth. No new real-data model
or segmentation trainer was added; baseline scientific validation remains required.
