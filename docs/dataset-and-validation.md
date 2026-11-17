# Microscopy dataset preparation

Target workstation: Windows 11 Pro, Intel i9-13900, 64 GB RAM, NVIDIA RTX 4090.
The application preserves its conventional CPU segmentation and measurements.
No pretrained model or CUDA dependency is included. An experimental object
classifier can be trained from reviewed corrections with separate held-out images;
see [manual review and learning](feedback-learning.md). Segmentation-model training
and scientific accuracy claims still require real-image validation and reviewed masks.

## Files and correspondence

Provide all 15 original images with their original filenames. Keep each labeled
reference alongside its original. Export ImageJ ROI Manager selections with
More > Save to retain the actual segmented-line coordinates in a ZIP. A numbered
JPG is a useful reference but is not an editable ROI set or a segmentation mask.
Do not analyze numbered/labeled copies as if they were originals.

The supported workbook has three conditions (RT30, 40C30, 40C1), five images per
condition, and 50 numbered records per image for each measurement type. Its pooled
copies are ignored. The imported records retain source worksheet/cell provenance.
Length and diameter remain separate; matching row numbers do not establish that
both measurements belong to the same fiber. Confirm the sampling method before
constructing per-fiber reference means or comparing diameter SD.

Use the app's image mapping CSV to associate each condition/image number with its
original filename, labeled filename, verified calibration, and optional ROI ZIP.
ImageJ ROI numbers and automated component IDs are independent. Explicitly match
fibers before using the paired comparison API. The mapping template does not
perform fiber matching automatically.

## Calibration and field of view

Record micrometers per pixel from ImageJ's Set Scale dialog for each ORIGINAL.
If recovering calibration from a bar, divide its physical length by the measured
pixel span in that exact image. A 20 µm bar does not establish a common µm/pixel
value across images that have been resized. Keep original dimensions and crops.
Exclude scale bars/text from the analytical field of view using a verified
same-resolution ROI/mask; do not interpret their dark lines as collagen.

## Annotation policy

Accept only obvious, clearly separated fibers. Bunched, overlapping, faint, or
border-truncated objects should remain review-required candidates and be excluded
from accepted-fiber statistics. Inspect accepted objects too: topology checks
cannot detect every merge. This rule expresses operator intent and does not prove
the current thresholding method already implements it accurately on these images.

Segmented-line ROIs can provide centerline references. For supervised segmentation,
reviewed outlines/masks must additionally define the fiber boundary (including
whether optical halos are excluded). Do not turn the 50 selected fibers into an
exhaustive mask and label every untraced fiber as background. Mark unknown regions
as ignored or complete a reviewed annotation of the selected training regions.

## Evaluation and future AI

Reserve whole images, and where possible whole independent specimen/acquisition
groups, for held-out testing before making patches. All originals, resized copies,
labeled copies, ROIs and patches of one image must remain in the same split.
Fifteen images support a pilot but do not establish generalization.

Compare accepted matched fibers using separate length and diameter paired counts,
bias and mean absolute error. Report missing measurements and review coverage.
Selected manual fibers are not an exhaustive count reference: unmatched automated
IDs do not automatically mean false positives. Test specimen variability,
segmentation boundaries, missed fibers, merges and repeat manual measurements.
No real-image accuracy result or trained-model claim has been established yet.

## Acceptance checklist before trusting measurements

1. Record the verified calibration for each original image, with its physical
   units. The app requires explicit entry and a decision on any proposed
   scale-bar/text exclusion. Verify masks and exclusion overlays at full detail.
2. Match individual automated IDs to ImageJ ROIs using the original coordinates.
   A shared row number or the same count of selected objects is not a match.
3. Confirm whether manual diameter values are single perpendicular widths or
   per-fiber means over several positions. Compare equivalent quantities. Agree
   on endpoints and whether optical halos belong to the measured boundary.
4. Set acceptable length/diameter errors with the scientific team before judging
   results. Use images or independent specimens not used to tune settings, and
   retain all originals/labels/patches of one image in a single evaluation group.
5. Report paired sample counts, bias, MAE, missing/excluded coverage and failure
   examples. Evaluate boundary quality and merges/splits alongside measurements.
   Fifty sampled fibers are not assumed to be a complete image count. Repeated
   fibers from one image are correlated; do not claim 750 independent specimens.
6. Repeat manual measurements on a subset to establish observer variability.
   If agreement is inadequate, refine on the development images and repeat the
   held-out evaluation before scientific use.

Passing software tests and exclusion checks on the supplied images does not
replace this paired validation. No numerical accuracy tolerance is claimed yet.
