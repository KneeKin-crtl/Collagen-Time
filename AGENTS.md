# Agent instructions

## Project
Build a research application for measuring collagen fibers in microscopy images.

## Rules
- Preserve original image resolution and user-supplied calibration.
- Keep segmentation separate from geometry, quality control, and export.
- Use weighted skeleton paths for curved length.
- Describe distance-transform diameter as an approximation.
- Count accepted isolated fibers separately from ambiguous candidates.
- Retain excluded candidates with explicit quality-control flags.
- Do not claim scientific accuracy without real-image validation.
- Do not implement model training until the baseline is validated.
- Never commit credentials or private microscopy datasets.

## Validation
From the repository root:
python -m pytest -q

Add meaningful tests for changed measurement behavior.
Report passed, failed, skipped, and unrun checks accurately.

## Delivery
Keep changes focused.
Explain what changed, why, and how it was tested.
Use a new branch and pull request for each development task.
