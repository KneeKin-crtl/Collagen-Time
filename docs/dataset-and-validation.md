# Microscopy dataset preparation

Target workstation: Windows 11 Pro, Intel i9-13900, 64 GB RAM, NVIDIA RTX 4090.
The current application is a conventional CPU baseline. No trained AI model or
CUDA dependency is included. Model training is deliberately deferred until the
baseline can be validated against real images and annotations are reviewed.

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
