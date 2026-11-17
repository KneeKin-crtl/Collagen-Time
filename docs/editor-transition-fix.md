# Browser check: image replacement and calibration

The Windows browser check could fail with `Review did not finish saving` after
uploading a second image and enabling the research diameter range. Reproduction
with 250 ms HTTP latency showed **no editor iframe** and the correct application
error: `Enter the verified calibration in µm/pixel before analyzing.`

Replacing an uploaded file briefly clears the old image. The check treated a blank
calibration in that temporary no-image form as the new image's calibration field,
typed into it, then submitted after the real new-image field reset to blank.
Analyze correctly refused the uncalibrated request; the browser check mislabeled
the missing editor as a save timeout. This was not a segmentation or measurement
calculation failure.

The updated check waits for a decoded-image marker keyed by the uploaded file's
SHA-256, then waits for Streamlit's completed render before entering calibration.
It verifies the submitted value and the committed editor's source hash, dimensions,
readiness and pending-save count. Identical filenames and dimensions are tested
with different pixel content, so an older image cannot satisfy the wait.

The page shows the loaded filename and original dimensions. Editor controls stay
disabled during initialization, and a pointer press from an older geometry cannot
be applied after an image change. Selection and measurement algorithms are unchanged.

Both native Windows workflows now exercise the browser checks with 250 ms HTTP
latency. A failure records editor state, source identity, app alerts, screenshot,
page text and server logs in a separately named `FAILED-browser-diagnostics-*`
artifact. These checks use generated fixtures only. Release binaries are still
published only after the packaged checks pass.

Reproduce the delayed-upload check with the optional `.[browser]` extra and an
installed Chrome/Chromium:

```bash
python tools/check_feedback_browser.py --network-latency-ms 250 --output-dir browser-results
```

Use `--executable` for a packaged analyzer or `--python` for a source environment.
Increasing a save timeout is not the fix: calibration must be entered into the
correct uploaded image's completed form.
