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
3. Click directly on a highlighted object to toggle accepted (green) / rejected
   (red). Zoom and scroll the primary view for small objects. Only the label at
   the original-pixel click is selected; a background click does nothing.
4. Use **Undo** on the image, Ctrl+Z while the image has focus, or **Undo last
   correction** in the object-ID controls. Undo survives closing the application.
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

`accepted_count` is the number of green accepted objects. `accepted_measurement_count`
and the legacy `accepted_isolated_count` count only accepted objects with positive,
finite length/diameter and no border, crossing/merge or excluded-region flag.
`accepted_pending_measurement_count` reports the difference. A manual acceptance
cannot reconstruct an ambiguous centerline or the missing end of a truncated fiber.
Such rows retain their original flags and unavailable/partial measurements; they
do not enter mean measurements or ImageJ error statistics. An accurate individual
outline may resolve a merged mask, but its quality depends on the operator.

Existing measurements are reused when classifications change. Manual outlines use
the same geometry function and calibration. Full per-fiber CSV exports include
`classification`, `manual_classification`, `original_classification`,
`baseline_classification`, `automatic_classification`, `decision_source`, model
versions, object/analysis keys, original-pixel bounding boxes, review-event IDs,
`used_for_training`, `used_for_evaluation`, `measurement_status`,
`included_in_measurement_statistics` and overlay visibility. Rejected detections
and fully covered originals remain in the full CSV. Use **Download measurable
accepted fibers CSV** for the accepted measurement dataset.

The QC archive retains the original binary segmentation, adds the reviewed labels,
and preserves `baseline_labels.tiff`. Manual cropped masks and their original-pixel
bounding boxes are in `manual_masks.npz` (read with `numpy.load(..., allow_pickle=False)`).
Original images retain their resolution and bit depth. Calibration changes scale
measurements without changing masks or review identity.

## Persistent storage and backup

On Windows, data are saved under
`%LOCALAPPDATA%\CollagenFiberAnalyzer\feedback\feedback.sqlite3`, outside the
downloaded source or executable directory. Linux uses
`$XDG_DATA_HOME/CollagenFiberAnalyzer/feedback`, falling back to
`~/.local/share/CollagenFiberAnalyzer/feedback`. Set `COLLAGEN_FEEDBACK_DIR` to use
another writable location; for cloud development use a directory outside the
checkout, such as `/workspace/collagen-feedback`.

SQLite transactions save each correction immediately. The database contains
original image pixels, exact cropped object masks, locations, initial automatic
decisions/model versions, append-only correction and Undo events, current labels,
and immutable training/evaluation membership per model version. No credentials,
network service, GPU, or third-party model download is needed.

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

Review at least **two training images and two separate evaluation images**, with
at least **five accepted and five rejected examples in each role**. Select
**Retrain Model Using Corrections**. This trains a small CPU logistic classifier
using shape, intensity contrast, pixel geometry, and QC features. Only current
explicit human labels are targets; predictions are never added as training labels.
Each image contributes equally within each class to reduce domination by many
fragments from a single image. Scaling is fitted on training examples only.

The candidate, active model, and baseline are evaluated against the same held-out
image examples. The candidate is promoted only if balanced accuracy improves by
at least one percentage point over both comparators without lower accepted
precision. Reports include false acceptance/rejection counts, class recall,
precision, image IDs and sample counts. Changes to corrections during training
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
