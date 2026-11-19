# Scientific validation audit

Baseline: commit `442328e` (the merged active-review feature). This audit precedes
new segmentation options; it does not establish real-image accuracy.

| Responsibility | Existing implementation | Interpretation / gap |
| --- | --- | --- |
| Original decoding | `loading.load_image` | Single-plane TIFF/PNG/JPEG, original pixels and coordinates retained |
| Analysis preprocessing | `preprocessing.normalize`, `preprocess` | Global min/max luminance normalization and optional Gaussian smoothing; no illumination model |
| Segmentation | `segmentation.ThresholdSegmenter.__call__` | Otsu/manual/local intensity thresholds; optional opening/closing, disabled by default |
| Candidate identity | `detection.detect` | 8-connected components; a component is not necessarily one biological fiber |
| Length | `geometry.skeleton_graph`, `measure` | Weighted open skeleton paths; branches/cycles retain missing geometry rather than misleading branch sums |
| Diameter | `geometry.measure` | Twice distance-to-background at retained interior centerline pixels; approximate, not exact perpendicular width |
| QC | `quality.classify`, `selection.apply_selection` | Area, topology, border, excluded regions and configurable physical/pixel screens; unusual curvature alone is not a biological rejection rule |
| Review and colors | `quality.review_approved_candidates`, `review.ReviewSession`, `assets/fiber_editor/editor.js` | Green is review approval with automatic fallback; red means unapproved/excluded, not proven non-fiber; dark blue is a temporary review cursor |
| Manual repair | `review.add_outline`, `feedback.FeedbackStore` | Explicit separated masks/missed objects, immutable baseline predictions, original-coordinate history and Undo/Redo |
| Existing learning | `learning.retrain` | Experimental CPU object classifier; no segmentation training or automatic recovery of absent candidates; image-held-out agreement is not specimen-held-out scientific validation |
| Existing comparison | `validation.compare_ground_truth`, `comparison_report` | Explicit image/ID paired, accepted-only bias and MAE; lacks independent instance matching, missed-object scoring, four-category reference labels and agreement plots |

## Inspectable data

The three condition archives contain 15 primary original JPEGs (five per
condition), selected numbered/labeled reference images and one labeled PDF.
The supplied workbook preserves separate length and diameter records and their
cell provenance. Its 50 selected records per image are neither exhaustive object
masks nor automatically paired per-fiber measurements. No independent masks,
specimen/acquisition mapping or complete per-image calibration table was found in
these inspectable files. The older `Fiber Count.zip` exceeds the execution tool's
32 MiB transfer limit, so its contents were not inspected in this task.

Only the reported Sample 3 calibration, 0.05813928 µm/pixel, was confirmed in the
conversation. It must not be assigned to all images. Private pixel-only baseline
inventory and CSVs are generated outside the repository; they do not assign fake
physical units or treat algorithm masks as reference truth. Scale-bar/text
exclusions also require confirmation before a scientific benchmark.

## Failure mechanisms to investigate

Global thresholding can fragment texture, include debris/annotation text, miss
weak fibers and merge touching structures. Background variation and halos can
shift boundaries. A merged component can sometimes have a deceptively simple
skeleton. Conversely, a small segmentation spur can block measurement of a real
fiber. Preserve these failure cases; biological class and algorithmic measurability
must be assessed separately. Do not infer correct recovery merely from a larger
candidate count or a larger accepted count.

## Incremental scope

1. Preserve the old default segmentation and a reproducible baseline profile.
2. Add independently versioned reference categories/boundaries and specimen-group
   metadata, separate from operational review decisions and immutable predictions.
3. Evaluate detection, boundaries, QC decisions and measurements separately, with
   partial-annotation and missing-value safeguards.
4. Offer conventional recovery settings as experimental candidates; compare them
   only on a common, independently annotated held-out scope.
5. Retain existing manual outline repair. Do not infer invisible crossing
   boundaries or add/train a neural segmenter without adequate annotations and
   an independent evaluation that can establish benefit.

Real-image precision, recall, IoU, measurement agreement, recovered true fibers
and new false positives remain unavailable until the required references exist.
