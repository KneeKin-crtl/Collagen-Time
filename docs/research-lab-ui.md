# Clean Research Lab interface

The website uses the existing Streamlit application and local canvas component.
The redesign changes presentation only. Segmentation, geometry, calibration,
selection criteria, review storage, model evaluation and export calculations are
unchanged. Both viewers still use the same original-pixel coordinate transforms.

The 42-pixel header graphic is the existing `assets/collagen.png`, embedded without
resizing its stored pixels, cropping or changing its transparency. Theme files,
system fonts and icons are local; no external font or image service is needed.

Settings are grouped into collapsible calibration, thresholding, morphology,
eligibility and advanced image-region sections. The form still submits only with
**Analyze image**. Collapsing sections does not disable or reset their controls.
The existing sidebar can be collapsed, and its settings scroll independently.

Summary cards display existing saved-result fields:

| Card | Existing field |
| --- | --- |
| Total detections | `candidate_count` |
| Reviewed objects | `manual_reviewed_count` |
| Measured fibers | `accepted_measurement_count` |
| Excluded / review | `review_required_count` |

Excluded / review includes automatic exclusions and review-required candidates;
it is not presented as a count of human rejections. Fields unavailable in a
read-only fallback are omitted. During pending saves, the image editor continues
to show its existing provisional counts and save status. Cards show confirmed
results after acknowledgment. Raw statistics remain in **Analysis summary details**.

The image toolbar groups existing review/outline, classification, history and
display controls. All element IDs, event handlers, mouse buttons, shortcuts,
zoom limits and scientific overlay colors are preserved. Narrow screens stack
the viewers; their displayed scales and aspect ratios remain matched.

**Measurements & results** prioritizes existing ID, measurement, eligibility and
exclusion columns visually, with unit labels. All existing columns remain
available, and the DataFrame and CSV content are unchanged. Ground-truth comparison,
four-stage QC images, learning/evaluation controls, backups and workbook review
remain available in their labeled expandable sections.

Presentation implementation:

- `assets/fiber_editor/lab-theme.css`: shared color/font tokens.
- `assets/lab-ui.css` and `collagen/ui.py`: Streamlit presentation.
- `assets/fiber_editor/editor.css` and `index.html`: canvas toolbar/viewer layout.
- `.streamlit/config.toml`: native source-run widget theme.
- `desktop.py` theme arguments and the Windows resource spec: the same theme and
  styles in frozen builds, independently of the launch directory.

Browser verification uses generated images and isolated review databases:

```bash
python tools/check_lab_browser.py --python /path/to/python --output-dir lab-ui-results
python tools/check_feedback_browser.py --python /path/to/python --network-latency-ms 250
```

Use `--executable /path/to/CollagenFiberAnalyzer.exe` for packaged Windows checks.
The UI check captures 1920, 1440, 1280 and 600-pixel layouts and verifies the logo,
control availability, saved card values, role locking, source pixels and CSV.
The interaction check exercises the existing correction and export workflows.
