# Persistent approval highlights

Left-click in **Review mode** saves approval of the exact detected mask. The
highlight uses #00FF00 at the existing 60% opacity over the source image. Right-click
saves rejection. Background clicks never choose a nearby mask. In **Outline mode**,
left-click adds a boundary vertex; a persistent mode indicator distinguishes these
actions. A newly approved mask does not clear earlier approvals.

## Cause and correction

The original event path received the click, mapped it to the correct source pixel
and fiber ID, and saved `manual_classification=accepted`. The display then derived
its green bit from `accept && can_accept`, where `can_accept` represented numerical
measurement eligibility. On confirmation the backend returned the same excluded
measurement status. Consequently, an approved but branched, tiny or filtered mask
could never stay green. Repainting that status did not fix this state coupling.

The review layer now derives a separate `review_approved` flag from saved human
labels, falling back to the automatic inclusion decision for unreviewed objects.
The browser projects that same approval flag immediately, paints only pixels with
the chosen segmentation ID, and repaints on saved confirmation. Reopening derives
approval again from SQLite; it does not depend on an old canvas or browser cache.
Superseded/hidden masks retain their labels in history but are not reapproved by
historical decisions after a newer manual outline owns their pixels.

Measurement eligibility still controls `included_in_measurement_statistics`,
the **Measured fibers** count, statistics and numeric image ID callouts. A green
approved mask can show **NOT counted** with its QC reason. Missing measurements
remain missing. Segmentation, calibration and centerline/diameter methods are
unchanged. Explicit acceptance/rejection labels used by the feedback classifier
are unchanged.

## Exports

- `review_approved` in the full CSV records the saved approval highlight.
- `included_in_measurement_statistics` records numerical inclusion independently.
- `review_overlay.tiff` matches the primary approval image, with measured ID labels.
- Existing `count_overlay.tiff` retains the measurement-inclusion view.
- The accepted-measurements CSV still excludes ineligible objects. No invented
  length or diameter is exported for an approved but unmeasurable mask.

## Diagnostics and verification

Enable **Click diagnostics** to inspect the last 40 local records: client and
original-image coordinates, detected ID, approval before/after, numerical inclusion,
masked redraws, and save confirmations. Logging is off by default, bounded in
memory, and is not written to the dataset or sent to an external service.

Generated-data browser checks cover an ambiguous 14-endpoint mask as well as
measurable fibers, multiple approvals, exact background handling, source-coordinate
mapping at 400% zoom, display redraws, mode changes, fresh-page restoration,
rejection/approval cycles, CSV inclusion and manual outline repair. Unit tests
independently verify mask-only blending, pure #00FF00 at full opacity, unchanged
source pixels, unchanged raw measurements, separate export overlays, filter changes,
and persistent Undo/Redo. No scientific accuracy claim follows from these checks.
