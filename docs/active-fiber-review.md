# Active fiber review highlight

**Guided review** is on by default. In Review mode, the first visible object
without a saved human label becomes the active target. Its exact segmentation
mask is highlighted in dark blue (`#00008B`) with a small white halo for contrast.
Only this one object receives the temporary highlight. The toolbar names the
same ID, and the Object ID dropdown shares the cursor.

Accept/Reject, A/R and classification clicks retain their existing decisions and
immediate green/red preview. Guided review moves to the next visible unreviewed
ID in ascending order, wrapping only among unreviewed objects. A queued decision
captures its own target ID before the cursor moves. A failed save restores the
failed object's target and confirmed decision. Once no unreviewed objects remain
in the current view, the cursor and blue layer clear and Accept/Reject disable;
Next never silently falls back to already reviewed objects in Guided review.

Next (N/P or arrow keys) and explicit Object ID choices can change the cursor
without saving a correction or creating a training example. The cursor is display
state, not a measurement or a stored classification. Filter changes keep a visible
target, or choose a visible unreviewed replacement when the target is hidden.
Clicking background clears the target without choosing a neighbor. Press Next
to resume. Entering Outline mode clears the blue layer; returning to Review mode
selects an unreviewed target again. Turn Guided review off to use the previous
free-form inspection/outline-repair workflow and its dashed selection bounds.

## Implementation

- `assets/fiber_editor/editor.js` uses the existing `state.selected` for toolbar
  actions and a bounded single-mask surface. It draws on the existing
  `outline-preview` canvas, in original image coordinates. That canvas is
  pointer-transparent and shares the source canvas's size, scale and scrolling.
- The preview is cleared before drawing a new target. Normal redraws, sprite
  confirmations, responsive resizing and iframe replacement restore the cursor
  from the existing browser state. Reopening a new browser page starts from the
  first remaining unreviewed object in that view.
- `collagen/review_ui.py` mirrors explicit Object ID changes with a transient
  `selection_request`. Selection-only component messages validate the context
  and target without calling `ReviewSession.correct` or writing review history.
  Accepted/rejected event payloads carry the next cursor separately from the
  object's immutable decision target.
- `data-active-fiber-id` on the main and preview canvases exposes the exact visual
  target for browser verification. The blue layer never changes the original
  canvas, green/red decision canvas, segmentation labels, ID layouts or exports.

The highlight is **steady, not animated**. There are no blink timers or animation
loops to leak across mode changes or iframe replacement. This prioritizes reliable
mask identity and avoids flicker. Green approval and numerical measurement
eligibility remain separate, as documented in [approval-highlights.md](approval-highlights.md).

## Verification

The existing browser harness exercises default initial selection, mask pixels,
non-target/background pixels, exact toolbar IDs, Object ID cursor synchronization,
both decision directions, completion, Undo/Redo, failed-save restoration, zoom,
scroll, resize, mode changes and unchanged source pixels. Its earlier free-form
review, outline, queue, persistence and export checks remain enabled with Guided
review explicitly switched off. App tests independently check Object ID advancement,
selection-only messages, rejected invalid cursor IDs and unchanged geometry/history.

Run the source browser harness with an isolated test database:

```bash
python tools/check_feedback_browser.py --python /path/to/python --network-latency-ms 250 --output-dir active-review-results
```

Use `--executable` on Windows for the packaged application. Tests use generated
fixtures only and do not establish scientific measurement accuracy.
