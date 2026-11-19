# Center the active review fiber

Each new review target is brought to the center of the existing **AI Fiber
Analysis** panel, retaining the current zoom and the temporary dark-blue mask.
The Original Microscope Image panel follows the same navigation and scale.
Use **Center active fiber (C)** to return after manually exploring; keyboard C
works while the image has focus and keyboard shortcuts are enabled.

Centering follows the initial target, Accept/Reject, classification clicks,
A/R, Next/previous and arrow keys, explicit Object ID choices, the controls under
Review by object ID, filter-driven target changes, Undo/Redo and failed-save
restoration. A confirmation or unrelated rerender of the same target does not
recenter a manually panned view. When review ends or no target is selected,
the blue mask clears and the Center button disables.

## What changed

Previously, `focusSelected()` used the segmentation bounding-box center, could
run before layout settled and was skipped by some target changes. Browser scroll
limits prevented border objects from reaching the center.

- `collagen/editor.py`: `review_focus_points()` supplies immutable original-pixel
  anchors once per geometry. `centerline_midpoint()` orders the existing open
  skeleton using the measurement graph's corner rule and diagonal step weights;
  it interpolates halfway along that path. Branched, cyclic, disconnected or
  missing centerlines use an actual mask pixel nearest the mask centroid instead.
  ID-text positions are never navigation anchors. No skeletonization or numerical
  measurement is rerun or changed for navigation.
- `assets/fiber_editor/editor.js`: `center_on_active_fiber()` observes the existing
  active ID, coalesces target changes into a layout-frame callback, converts the
  original-pixel anchor into current display coordinates, and synchronizes the
  two scroll panels. Pending centering survives iframe replacement; completed
  centering does not repeat for confirmation messages. Explicit recentering is
  available through the button or C.
- `resize()` preserves the point at the viewport center through zoom/layout
  changes. A ResizeObserver handles column/sidebar layout changes too.
- `index.html` and `editor.css`: both image panels use a display stage with
  half-viewport margins. These margins let every image edge scroll to the center.
  Original canvas dimensions, source coordinates, masks, overlays and ID glyphs
  are untouched. The preview shares the exact canvas offset and scale.
- Browser test coordinate helpers account for the display stage offset when
  driving real mouse clicks and outline drawing.

There is no smooth-scroll animation or blink timer. Navigation remains immediate,
while the existing blue highlight stays steady. Manual panning does not trigger
recentering; zooming retains the current viewed location until the target changes
or recentering is explicitly requested.

## Verification

Unit tests cover weighted diagonal paths, asymmetric curved paths, border points,
mask fallbacks and unchanged input arrays/tables. The new browser check uses six
synthetic objects across all four image edges, a curved fiber and a branched mask.
It checks centering at 50%, 100%, 150%, 400% and 800%, real click coordinate mapping,
review advancement, filters, the Object ID dropdown, manual pan preservation,
responsive layouts, save failures, exports and reopening.

```bash
.venv/bin/python -m pytest -q
python tools/check_centering_browser.py --python /absolute/path/to/.venv/bin/python --network-latency-ms 250 --output-dir feedback-browser-results/centering
python tools/check_feedback_browser.py --python /absolute/path/to/.venv/bin/python --network-latency-ms 250
```

Browser checks need the developer browser extra and Chrome/Chromium. On Windows,
use `--executable dist/CollagenFiberAnalyzer/CollagenFiberAnalyzer.exe` instead of
`--python` to test a packaged build. The Windows workflows run the centering check
alongside existing review and reference checks. These are software interaction
tests on synthetic data, not evidence of real-image scientific accuracy.

## Viewer refresh recovery

The Windows centering check exposed an iframe-lifecycle problem: the completed
centering key survives in parent-tab state, but the DOM marker used by diagnostics
is lost with the old iframe. The unchanged-target path now restores that marker
without moving a view the operator may have manually panned.

A forced-refresh regression also exposed cached image-load promises owned by the
old document. Reusing such a promise after iframe destruction can stall restoration.
The cache now retains resolved geometry rather than completed iframe promises;
only the current document can reuse an in-flight load. An abandoned load requests
fresh immutable layers from the existing backend, without rerunning measurement.

The browser check now reloads the actual viewer document, verifies centering and
manual-pan preservation, and simulates an abandoned load to verify recovery. It
retains the original centering tolerance and still checks actual on-screen offsets,
mask identity, zoom, saved decisions and numerical exports. A missing diagnostic
marker is never treated as evidence that measurements changed.
