# PyTie calculator lab

A focused ROM-free TI-84 Plus display/input runner, repurposing the same
`@specy/z80` assembler and CPU sources used by ASM Editor. This is a standalone
frontend in the ASM Editor checkout, not a new mode inside its full Svelte IDE.
It builds independently, without the Java, Rust or other CPU toolchains.

## Run

From this directory, with Node.js 20 or newer:

```
npm install --workspaces=false
npm test
npm start
```

Compile now uses the Python modules in `../../tipy`, through the local
`POST /api/compile` endpoint. Set `TIPY_PYTHON` to your Python 3.10+ executable
before `npm start` if it is not available as `python` on PATH. There is no
JavaScript compiler fallback. Plain `.asm` and `.z80` entries still use the
browser assembler directly.

TiPy compilation defaults to aggressive multi-pass optimization. Deterministic
functions, lists and drawing can be evaluated at compile time and replaced with
compact native output. Programs with no observable output can become a single
RET; only used runtime helpers are linked. Render ordering, unknown initial
framebuffer bits, runtime errors and termination are preserved. See the root
README for optimization modes, reports, budgets and the 58-byte checkerboard
benchmark. Original source declarations are returned in `source_symbols`, and
`symbols` contains only the storage that survives optimization.

Open http://127.0.0.1:8084. Edit `main.tip` and click **Compile**. The compiler opens
a read-only **Z80 assembly** tab and reports the assembled size without executing
or switching the selected tab. Click **Z80 assembly** to inspect the output.
Click **Run** to start the program on the calculator screen. Run starts a fresh
execution; Pause/Resume controls an active execution. Editing source or changing
the entry file disables Run until the project is compiled again.
Source tabs remain available. The plain layout follows the supplied `UI.png`:
files on the left, editor in the middle, screen and keypad at right, Compile below.

The default example uses declarations and arithmetic supported by Python; it
computes values but draws nothing. Existing saved projects are not overwritten.
Click the screen before using your keyboard for programs that poll the keypad.
Ctrl is 2nd, Shift is Alpha, Escape/Backspace is Clear. Pointer cancellation,
focus loss and pause clear held input. Multiple keys can be held at once.

Python programs draw into `plotSScreen` at `0x9340` through native Z80 with
`pixel_on(x, y)`, `pixel_off(x, y)`, `flip_pixel(x, y)`,
`h_line(x1, y1, x2, y2)`, `v_line(x1, y1, x2, y2)`,
`line(x1, y1, x2, y2)` and `rectangle(x1, x2, y1, y2, width, filled)`.
`clear()` clears that buffer and `render()` invokes the TI-OS `_GrBufCpy` bcall
to update the LCD. Drawing never automatically renders or clears.
Coordinates are inclusive from `(0, 0)` to `(95, 63)`; off-screen pixels are
ignored and reversed endpoints are supported. Axis lines require aligned
endpoints. Rectangle width is inward border thickness; filled is a required
bool and ignores width when True. See `../../examples/pixels.tip` and the root
README for clipping details.
The file list and tabs preserve files in browser storage; Open file imports
`.tip`, `.asm` or `.z80`. Click a file name to open it. Click its circle in the
file list to set it as the entry file; its tab gains an ENTRY badge. Compile
always uses that entry, independently of the active tab. Entry selection persists
on refresh. `import utils` includes `utils.tip`. Raw assembly can also be an entry.
Older saved `.pti` projects migrate to `.tip` without discarding their contents.

`npm run build` makes the self-contained web assets in `dist/`. No CDN is needed.
The checked-out `../packages/z80` submodule must be present. Build aliases point
directly at those sources rather than silently substituting another emulator.
The build also corrects the upstream assembler's RST-vector radix: its instruction
database uses hexadecimal vector names, which its trie otherwise reads as decimal.
This narrowly scoped transformation lives in `build.mjs`; the submodule stays clean.

## TiPy Python compiler

The small compiler supports explicit `int`/`i16`, `u8`, and `bool` declarations,
assignment, addition/subtraction, nested expressions, comparisons, indented
`if`/`else`, `while`, `break`, `continue`, `pass`, and top-level `import`.
Comparisons produce bool; signed ordering accounts for overflow. Nested loops
maintain separate condition/exit labels. Typed functions, positional calls, locals,
return values, `void`, forward calls and recursion use per-call IX stack frames.
Semicolons are rejected. A variable declared only inside a branch or loop cannot be read afterwards
without guaranteed initialization.
Out-of-range constant assignments and bool/integer mismatches are compile errors.

Graphics and input builtins from the previous JavaScript prototype have not yet
been ported to Python. Existing code using them reports errors; it does not
silently run through another compiler. Raw assembly can use the hardware below.

This is a first compiler subset, not the complete language. Nested definitions,
closures, `and`/`or`, multiplication/division, and full block scopes are
not implemented. Globals use fixed storage; parameters/locals use a per-call
frame. Dynamic arithmetic currently wraps to the
storage width; that is a prototype limitation, not a finalized PyTie overflow rule.

Typed arrays use an initializer such as `int scores = [10, 20, 30]`, without an
`int[]` declaration. Indexing/assignment, negative indexes, `len`, append, insert,
pop, remove, clear, extend, reverse, copy, count and index compile to Z80 runtime
routines. Descriptors store length for O(1) len. An 8 KiB reusable heap holds
descriptors and contiguous element blocks; reference counts release replaced
arrays and local arrays on function return. Array assignment shares a descriptor;
copy creates independent data. Bounds/allocation failures are shown in status.
Only one-dimensional scalar arrays are implemented. Typed array parameters such
as `int[] x` share a retained descriptor, and `for num in x` copies elements into
an inferred scalar loop variable. Nested iteration, break/continue, and early
returns clean up iterator references. Array return types are not yet supported.
See the workspace README for memory limits and semantics.

## Hardware contract

- Executes assembled Z80 bytes, rather than interpreting source drawing commands.
- 64 KB CPU address space. CPU RAM is writable at `0x8000–0xFFFF`. The lower
  half uses replacement flash pages, with port 6 selecting the `0x4000` bank.
  This is not a complete TI-84 memory controller; banked RAM and alternate mapping
  modes are not implemented. Unknown ROM execution stops.
- Default code address is specified by `org userMem` (`0x9D95`) in examples.
  Stack begins at `0xFFFE`; IY starts at the TI flag area (`0x89F0`).
- `plotSScreen` is `0x9340–0x963F`: 768 bytes, 12 bytes per row, bit 7 leftmost.
  Stores here do **not** redraw the LCD. `_GrBufCpy` explicitly copies to LCD RAM.
- Independent 96 × 64 monochrome LCD. Port `0x10` accepts addressing/control
  commands; `0x11` reads/writes LCD data. Supports 6/8-bit words, automatic row or
  column increment/decrement, display enable, and a dummy-read latch.
  Busy waits are instantaneous; hidden LCD columns, analog response, contrast
  appearance and all controller commands are not fully emulated.
- Port 1 implements the active-low TI keypad matrix: write a group mask, then
  read combined selected groups. ON uses the interrupt path rather than the matrix.
- Approximate 6 MHz execution pacing and 118 Hz timer interrupts. IM1 has a
  replacement EI/RETI handler. These timings and OS-call cycle costs are approximate,
  so this is not a cycle-accurate or grayscale validation environment.

## Replacement OS

`#include "ti84.inc"` supplies the supported symbols. A standard bcall is:

```
        rst 0x28
        dw _GrBufCpy
```

Supported: `_GetCSC`, `_PutMap`, `_PutC`, `_DispHL`, `_PutS`, `_ClrLCDFull`,
`_ClrScrnFull`, `_HomeUp`, `_GrBufCpy`, `_GrBufClr`, `_MemSet`.

These are intentionally small replacement implementations, not the TI operating
system. The preview font is an original 5×7 ASCII subset, lowercase is rendered
uppercase, and OS register/flag side effects and text flags are not fully matched.
Small variable-width text, TI-BASIC, the OS shell, filesystem, USB/link hardware,
full timer/controller ports and banked RAM are not supported. Unknown OS calls,
ports and LCD commands produce explicit errors. No rewind is exposed because
peripheral and OS state is not journaled with CPU history.

Use this to develop and inspect PyTie graphics/input against a documented subset.
Successful preview execution is not proof of full compatibility with a physical
TI-84 Plus; eventual device or complete-emulator testing is still needed.

## Attribution

- Parent: [ASM Editor](https://github.com/Specy/asm-editor), AGPL-3.0.
- Assembler and CPU: [Specy/trs80](https://github.com/Specy/trs80), the MIT-licensed
  `z80-machine` and accompanying Z80 packages. Their source and license files are
  retained in the submodule; no TI ROM is included.
- Hardware reference: [WikiTI](https://wikiti.brandonw.net/index.php?title=Category:83Plus:Ports)
  and inspection of [tihle](https://github.com/tari/tihle).

New runner code is provided under the parent project's AGPL-3.0 license.

## Verification

`npm test` builds the runner and runs assembled programs to verify graphics-buffer
separation, LCD-port output, simultaneous keypad groups, keyboard-driven movement,
text output, unsupported-operation errors, LCD read-latch behavior and diagnostics.
It also compiles PyTie programs and verifies their real Z80 execution, imports,
types, signed comparisons, loops, clipping and keyboard-driven drawing.

For optional UI checks, install Playwright separately and run `node browser-test.cjs`
with Microsoft Edge available. `PLAYWRIGHT_MODULE` can point to a preinstalled
Playwright package. The preview server must already be running. This checks keyboard
and pointer input, release outside the button, generated Z80 output, source tabs,
imported file compilation, failed builds, persistence, desktop/mobile screenshots
and absence of browser JavaScript errors.
