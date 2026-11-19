# Technical baseline and present scientific limits

The frozen starting point is commit `442328e`. Before changing defaults, the
existing algorithm was run on the 15 primary original JPEGs found in the three
condition archives (five RT 30 min, five 40°C 30 min, five 40°C 1 h). Labeled copies
and alternate exports were not substituted for originals. Private source pixels,
CSV inventories and reports remain outside the repository.

The inventory used dark Otsu, no smoothing or morphology, minimum component area
20 pixels and two-pixel endpoint exclusion. No unverified physical calibration or
scale-bar/text exclusion was invented. All 15 updated **default segmentation masks
were pixel-identical** to the frozen pre-change segmenter on those originals.
This establishes default compatibility, not segmentation accuracy.

Three originals were also processed with the experimental background-subtraction
candidate (sigma 25 pixels), using the same geometry/QC definitions:

| Original image | Original candidates | Candidate candidates |
| --- | ---: | ---: |
| 1hr at 40C 40x Sample 1.jpg | 3,853 | 5,524 |
| 30minat 40C 40x Sample 3.jpg | 5,964 | 1,418 |
| 40x RT 30min.jpg | 4,039 | 4,512 |

These are connected components, not independently verified biological fibers.
Both increases and decreases occurred. Neither indicates correct recovery or
reduced false positives without reference annotation. No real-image sensitivity,
precision, IoU, recovered-fiber count or measurement error is claimed. The report
marks those metrics **Unavailable** and the ML comparison **Not evaluated**.
Runtime was recorded for traceability, not as a controlled performance benchmark.

The confirmed 0.05813928 µm/pixel calibration is recorded only for the reported
Sample 3 original; the other image entries remain uncalibrated until verified.
Different magnification or bar pixel span must have its own calibration. Specimen
and acquisition identities are unknown. The 15 inspected images have been exposed
to development and are not retrospectively assigned to a blind final test.

## Exactly what is still needed

- Specimen IDs and acquisition-session/group IDs, including links between conditions
  or repeated fields of one specimen; sufficient independent fresh groups for final
  testing, separate from parameter selection and model promotion.
- Verified per-original µm/pixel values and their measured scale-bar spans/source,
  plus confirmed original-coordinate analytical regions excluding annotation text.
- Independently reviewed boundaries and the four biological/measurability categories,
  including weak/missed fibers, debris, overlapping/truncated objects and explicit
  ambiguous regions. State exhaustive versus partial coverage accurately.
- Explicit per-fiber matching for ImageJ reference lengths and mean diameters, with
  endpoints/width conventions and cell/ROI provenance. Existing separate workbook
  rows are not automatically paired, and selected 50/image are not complete masks.
- Repeat manual measurements on a subset to assess observer variability, and agreed
  scientific length/diameter error tolerances before judging adequacy.

No independent masks or complete grouping/calibration table were confirmed in the
inspectable archives/workbook. The older `Fiber Count.zip` could not be inspected
because it exceeds the tool's 32 MiB transfer limit; this is not a claim about its
contents. The user's real Windows feedback database has not been supplied and is
not represented by the isolated synthetic QA stores.

Use the [validation guide](scientific-validation.md) to collect these references,
freeze profiles and run the same-image, group-disjoint comparison. Until then,
conventional recovery settings remain opt-in experiments and no new neural model
is trained.
