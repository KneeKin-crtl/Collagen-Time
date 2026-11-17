# Review latency benchmark — 2026-11-17

Controlled software benchmark on the same cloud Linux machine using installed
Chromium 151.0.7922.173 and the stable Python 3.12 environment. Before: commit
`3483680`; after: `fiber-selection-review-speed`. This measures interaction latency,
not segmentation accuracy, biological validity or native Windows performance.

Generated image: 4096 × 3072, 400 separated bright components, calibration 0.02
µm/pixel for this synthetic case. Browser viewport: 1500 × 1100. Eight sequential
clicks on the same object toggle acceptance; the optimized editor uses its optional
legacy toggle mode so both runs perform equivalent operations. Every sample waits
for the previous correction to be acknowledged. Initialization is outside the click
timings; the first correction is included. Temporary databases contain generated
data only. Segmentation and geometry calculations are unchanged between versions.

| Metric | Before | After |
| --- | ---: | ---: |
| Median visual response | 4,460.00 ms | 29.25 ms |
| 95th percentile visual response | 7,948.20 ms | 158.40 ms |
| Median save acknowledgment | 4,460.25 ms | 505.50 ms |
| 95th percentile save acknowledgment | 7,948.50 ms | 1,059.70 ms |

Visual response measures `pointerup` to the animation frame after the requested
pixel changes. Save acknowledgment requires the confirmed render and no pending
queue entries. With eight samples, nearest-rank P95 equals the maximum; these are
small-sample observations rather than a population latency estimate. Optimistic
colors/counts are provisional until acknowledgment. Tests independently exercise
failed-save rollback, ordered queued saves, Undo/Redo and final CSV inclusion.

The median visual response improved by about **152×**. Seven of eight optimized
samples were below 100 ms; the 158.40 ms outlier is retained, not discarded. Save
latency improved separately and remains higher than the immediate visual update.
Other images, machines, browser load and storage can produce different timings.
No timing threshold is enforced in normal tests.

Raw samples: [before](benchmarks/review-before.json) and
[after](benchmarks/review-after.json). Run the reusable benchmark with:

```bash
python tools/benchmark_review.py --python /path/to/environment/python --output-dir review-benchmark
```

Install the optional `.[browser]` extra first. Windows uses installed Chrome;
Linux can use an installed Chromium or `--browser-executable`. The command also
accepts the browser check tool's `--executable` option for a frozen application.
