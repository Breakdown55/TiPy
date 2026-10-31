# Native circle benchmark

From `asm-editor/ti84-runner`, build and run the optional benchmark:

```powershell
node build.mjs
$env:TIPY_CIRCLE_BENCHMARK = '1'
node --test --test-name-pattern='Circle algorithm comparison' dist/python-compiler.test.mjs
```

Then run `python experiments/render_circle_benchmark.py` from the repository
root to produce `asm-editor/ti84-runner/dist/circle-benchmark.html`.

The reference uses `tipy/shape_runtime.py` from commit `f1f86cd`; fetching that
commit is required in a shallow clone. The benchmark is opt-in so ordinary CI
does not need historical source. The historical and full-walk implementations
are benchmark-only. The current compiler selects Bresenham or a viewport arc
scan without changing their pixels.

The workload is the user's diameter-20 moving-circle loop, capped at eight
frames with Right held. An initial render establishes the timing boundary;
the first drawing frame is recorded separately from seven steady-state frames.
Clear, input polling, loop work, and render are included. FPS comes from native
Z80 T-states at nominal 6/15 MHz, rather than host wall-clock speed or the
browser's display refresh limit.

Pixel accuracy is reported two ways: changed pixels compared with the old
outline, and absolute radial distance of outline pixel centers from an ideal
circle. A different rasterization convention can change many pixels while
improving radial accuracy. The data includes radii 5, 10, and 20 for mask
comparisons; displayed radial error is for the moving radius-10 circle.

For the dense size sweep and large-radius stress cases, run:

```powershell
node build.mjs
$env:TIPY_CIRCLE_SWEEP = '1'
node --test --test-name-pattern='Viewport circle arc candidate|Circle size sweep' dist/python-compiler.test.mjs
$env:TIPY_CIRCLE_SWEEP_VARIANT = 'adaptive'
node --test --test-name-pattern='Circle size sweep' dist/python-compiler.test.mjs
```

The first sweep compares the original, full Bresenham walk, and pure visible
arc path. The second adds the production adaptive renderer. Set the variant
variable to select just one implementation; clear it to rerun the comparison.
Run `python experiments/render_circle_sweep.py` from the repository root for
the standalone `dist/circle-sweep.html` report next to its JSON measurements.

Each implementation is checked against an independent pixel reference for
filled and outlined circles, every radius 0–128 at centered and top-cap
placements, then radii 256, 512, 1024, 4096, 16384 and 32767 at centered,
top-cap and left-arc placements. Every frame includes clear and render. Dense
frames also include loop dispatch. Large full walks stop after 40 million
T-states; their reported FPS is an upper bound, not a completed measurement.

Production routing first rejects offscreen bounds or handles full viewport
coverage. It chooses a short Bresenham walk below a measured cost threshold
(radius < 12 times the shorter visible dimension for fill, 18 for outline),
otherwise scans visible rows or columns. Radii >=8192 always use arcs to avoid
overflow in the short walk's signed 16-bit decision variable. The arc extents
use integer square roots and reproduce midpoint/Bresenham pixels exactly.
This is a cost heuristic; it does not promise the fastest choice at every size.
