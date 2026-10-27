# TiPy

TiPy is a Python-inspired, statically typed language targeting the TI-84 Plus Z80.

Its browser editor compiles TiPy to native Z80 assembly and runs it in a ROM-free
TI-84 Plus emulator with a 96×64 display and calculator input.

Clone with `git clone --recurse-submodules <repository-url>` to include the pinned
Z80 assembler/CPU library. For an existing clone, run
`git submodule update --init --recursive`. The editor lives in
`asm-editor/ti84-runner`; see its README for build and server instructions.
The rest of the original ASM Editor checkout is a local research dependency
and is not part of this repository.

Browser projects support qualified imported function calls:
`import utils` followed by `utils.get_evens(x)`. Function names are scoped per
file and resolved to direct calls at compile time. Functions in an imported file
can call other functions in that file without a prefix. Existing bare imported
function calls work when unambiguous; qualify the call if multiple imports export
the same name. Imported module aliases cannot be reused as variable names.
Global variables still use the existing shared include storage; module-qualified
global-variable access and runtime module objects are not implemented.
Selective function imports are also supported:
`from utils import get_evens`, comma-separated names, and aliases such as
`from utils import get_evens as evens`. Unselected functions are not bound in
the importing file, and unused imported functions add no generated code.

Single-line numeric/boolean constants can use `#define HEIGHT 64` or
`#define HEIGHT (BASE * 2)`. Constants are file-scoped, case-sensitive, expanded
as grouped tokens before parsing, and require no runtime storage. Definitions
must precede use and can refer to other visible defines. Recursive expansions,
conflicting redefinitions, and malformed definitions report source locations.
Runtime variables, function-like macros, macro imports, and textual/string
substitution are not part of `#define` support.
Compiler stages remain inspectable: tokens, parsed AST, checked AST, optimized
IR, optimization reports and generated Z80. The compiler uses only Python's
standard library (Python 3.10 or newer).

## Optimization pipeline

The site and CLI default to **aggressive** optimization. Observable behavior is
the sequence of rendered frames, runtime failures, and whether the program
terminates. Unused variables, intermediate lists, unrendered buffer writes and
pure computation are removable. Internal RAM contents, allocation counts and
instruction timing are not an external interface in this mode. Allocations can
be eliminated, so optimized programs may avoid resource exhaustion entirely.

The pipeline performs function reachability and effect analysis, bounded
whole-program partial evaluation, constant propagation and branch pruning,
pure-function/list evaluation, dead-state elimination, small-function inlining,
constant-range strength reduction, stack-frame compaction, graphics loop
batching, runtime dependency linking and assembly peepholes. Constant-propagation,
dead-state and inlining passes repeat up to three times to expose further work.

Pure filtering functions with known inputs can become constant lists. When a
list is still needed at runtime, a deduplicated data blob initializes a fresh
mutable list with preallocated capacity and one bulk copy instead of repeated
append calls.
Alias analysis and mutation tracking prevent replacing a shared list with an
independent copy, and prevent treating transient append/pop mutations as pure.

Deterministic drawing can collapse to static framebuffer operations with no
runtime lists, user functions, drawing loops, or heap. The framebuffer backend chooses among
changed-byte patches, single-bit SET/RES, fills, fills with sparse patches,
repeating patterns and run-length encoding. It preserves each render boundary
and keeps untouched bits symbolic, so it does not assume calculator RAM is zero.
Unknown branches invalidate buffer facts; runtime errors and unsupported effects
decline specialization rather than being erased. Evaluation is bounded by
operation, recursion, container and frame limits; programs that exceed them use
the conservative runtime path.

For the even/odd checkerboard: original code used 11,175 assembled bytes and
3,000,201 emulator T-states; row batching used 11,376 bytes and 905,426 T-states;
aggressive specialization uses **58 bytes, zero heap, and 76,288 T-states**.
It emits a 24-byte repeating pattern and native buffer copies followed by the
usual TI-OS LCD copy. No Python interpreter or host-side rendering is involved.

Modes are selectable through the CLI, Python API, or compile endpoint:

```powershell
python -m tipy examples/pixels.tip --optimization aggressive
python -m tipy examples/get_evens.tip --optimization balanced --show optimized
python -m tipy examples/get_evens.tip --optimization reference
python -m tipy examples/pixels.tip --show optimization_report
```

`balanced` preserves runtime globals for inspection while folding pure work and
using constant-data list construction. `reference` retains the previous runtime
code-generation path for learning, ABI checks and differential tests, including
its established peepholes and optional row batching. Python API calls accept
`optimization="reference"`; the JSON compile endpoint accepts the same optional
`optimization` field. `symbols` describes emitted storage, while `source_symbols`
retains original declarations. `checked` stays unchanged by optimization.

## Try it

`examples/fireworks.tip` is a complete animated example. It uses the built-in
`clear()` and explicit `render()` calls: clear the buffer, draw, render, then
sleep. Neither drawing nor `time.sleep()` implicitly updates the LCD.

Native standard-library support includes `random.randint(low, high)` (both
endpoints included), `random.seed(integer)`, `time.sleep(seconds)`, and
`math.radians(integer_degrees)`, `math.sin(radians)`, `math.cos(radians)`.
Module imports, aliases and selective imports resolve at compile time, including
imports inside functions/loops. Only used routines/tables are included. Random
draws and delays remain runtime effects and cannot be folded away.

Unannotated assignments infer a static storage type from their first value;
later assignments must keep that type. Unannotated function parameters default
to `i16`; a missing return annotation means `void` without valued returns, or
`i16` with valued returns. Use explicit annotations for other parameter/return
types. Locals shadow globals. Integer chained comparisons evaluate each operand
once and stop at the first false comparison.

Decimal literals and trig results use signed **Q8.8 fixed point**, with 1/256
resolution and range −128 through 127.996, rather than Python floating point.
`int(value)` truncates toward zero. Mixed integer/fixed addition, subtraction,
comparison and multiplication are supported; fixed-by-fixed multiplication is
currently rejected. Intermediate fixed-point values must fit that range;
runtime arithmetic wraps. Trig uses shared lookup tables. Literal sleep values
retain decimal precision to whole milliseconds; variable fixed-point sleep
values use their 1/256-second resolution. Sleeps accept 0–32.767 seconds and
use a native busy wait calibrated for the calculator's 6/15 MHz setting, so
timing is approximate and draw/computation time is additional.

Python-style features compile to native Z80; no Python interpreter runs on the
calculator. The supported subset now includes:

- `range(stop)`, `range(start, stop)`, `range(start, stop, step)` with an
  exclusive stop and positive or negative steps. Zero steps are errors.
- `list()`, `list(array)` (independent copy), and `list(range(...))`.
- `abs(number)`, `sum(array[, start])`, `min(array)`, `max(array)`, numeric
  `min(a, b, ...)` / `max(a, b, ...)`, `any(array)`, and `all(array)`.
- `for index, value in enumerate(array[, start])` and
  `for a, b in zip(first, second)` (also more than two inputs). Zip stops at
  the shortest input. These forms currently require unpacked loop or
  comprehension targets; tuple values are not implemented.
- List comprehensions with filters, multiple for clauses, and nested
  comprehensions, including `[n*n for n in range(10) if n % 2 == 0]`.
  Comprehension targets are private and do not overwrite surrounding variables.
- `elif`, short-circuit `and` / `or`, `not`, `in` / `not in`, `*`, `//`, and
  `*=`, `//=`, `%=`. Boolean operators require bool operands. Floor division
  and remainder follow Python's sign convention; division by zero is an error.

Only reachable functions and the runtime helpers they use are emitted. Unused
builtins, graphics routines, and their dependencies add no machine code. A
direct `for n in range(...)` uses a counter loop and needs no list heap.
In value contexts, `range(...)` currently materializes an i16 list, so it uses
the existing 8 KiB heap and allocation limits; `list(range(...))` avoids making
a redundant copy. Enumerating or zipping a range currently materializes that
input too. Empty `sum`, `any`, and `all` return 0, False, and True respectively;
empty `min` / `max` raise a runtime error. Arithmetic remains i16 and can wrap
at runtime. This is a typed Python-inspired subset, not CPython compatibility;
keyword arguments, arbitrary Python packages and object types are not supported.

See `examples/python_builtins.tip` for a runnable example.

In the browser IDE, **Compile** shows generated assembly and its size. **Run**
executes the compiled program. Source edits require recompilation before Run.

```python
clear()
pixel_on(10, 20)
flip_pixel(10, 20)
pixel_off(10, 20)
h_line(0, 10, 95, 10)
v_line(20, 0, 20, 63)
line(0, 0, 95, 63)
rectangle(10, 40, 20, 50, 2, False)
render()
```

Drawing builtins return `void`. `(0, 0)` is the top-left pixel of the 96x64
screen. `clear()` clears the 768-byte graphics buffer; drawing changes only that
buffer. `render()` copies it to the LCD. There is no automatic clearing or
rendering, including at program exit.

`h_line(x1, y1, x2, y2)` requires equal y coordinates; `v_line` requires equal x
coordinates. Misaligned axis lines draw nothing. `line(x1, y1, x2, y2)` handles
any direction with integer Bresenham-style rasterization. All endpoints are
inclusive and reversed endpoints work. Off-screen pixels are ignored; axis
lines and rectangles clip their iteration ranges to the screen. Arbitrary
lines step along their full major-axis range, so very distant off-screen
endpoints cost more work.

`rectangle(x1, x2, y1, y2, width, filled)` uses inclusive opposite corners.
`width` is inward border thickness in pixels and `filled` is a required bool.
Filled rectangles ignore width; non-filled rectangles with width <= 0 draw
nothing. Reversed corners work, and clipping preserves the original border.
Only the drawing routines a program uses are emitted. `examples/pixels.tip`
shows these operations together.

Run these from this workspace directory:

```sh
python -m tipy examples/declarations.tip
python -m tipy examples/declarations.tip --show tokens
python -m tipy examples/declarations.tip --show ast
python -m tipy examples/declarations.tip --show checked
python -m tipy examples/declarations.tip --show symbols
python -m tipy examples/declarations.tip -o main.asm
```

Use your Python executable in place of `python` if necessary. In this Codex
session the available full Python runtime is:

```powershell
& 'C:/Users/XBbur/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m tipy examples/declarations.tip
```

## Stages

| File | Input | Output / responsibility |
|---|---|---|
| `tipy/lexer.py` | Source text | Tokens with source locations |
| `tipy/parser.py` | Tokens | Dictionary syntax tree; groups expressions |
| `tipy/checker.py` | Tree | Checked copy and symbol table; resolves `int` to `i16` |
| `tipy/codegen.py` | Checked tree and symbols | Z80 assembly and labeled variable storage |
| `tipy/compiler.py` | Source text | Connects the stages and retains each output |
| `tipy/__main__.py` | CLI arguments / file | Prints a selected stage or writes `.asm` |

Syntax nodes are ordinary dictionaries: `declare`, `assign`, `number`, `boolean`,
`name`, `add`, `subtract`, and `negate`. There is no `Expr` base class to learn.
The parser keeps `name` as text; the checker adds a separate `symbol` label.

The expression convention is **result in HL**. Binary operations save the left
result on the stack while generating the right expression. The generator then
places the right result in DE, restores HL and performs the operation. Assignment
stores HL into an existing slot; declaration also reserves that slot. Expression
stack temporaries do not overwrite the source variables.

Labels are resolved to addresses by the assembler, not the Python symbol table.
Code begins at `0x9D95` and returns before the variable storage area.

The assembly optimizer removes redundant stack save/restore pairs and jumps to
the next label, and loads simple second operands directly into DE. List lengths
are read directly from descriptors. For-in loops read elements directly after
their loop bounds check, while still reloading the current length and data
pointer each iteration so mutation and resizing remain supported. Comparisons
such as `num % 2 == 0` branch directly on the mask result. Runtime expressions
use small pointer increments and register doubling where possible.

The drawing optimizer also recognizes repeated row loops containing invariant
list-driven `pixel_on`, `pixel_off`, or `flip_pixel` operations, optionally under
pure conditions. It builds reusable 12-byte row masks before the loop and then
uses byte operations on the graphics buffer. XOR masks preserve duplicate-pixel
parity; on/off masks preserve idempotence. Pixel and row loop variables retain
their original final values, including empty lists and off-screen coordinates.
Existing framebuffer bits are preserved according to the drawing operation.
The pass skips mutation, function-call, early-exit and rendering barriers,
known row loops with fewer than four iterations, and function frames without
room for their scratch buffers. It is a general loop idiom, not a special case
for checkerboard constants. Scratch masks are rebuilt on each entry to the
region, so mutations outside it remain visible.

For the 96x64 checkerboard with filtered even/odd coordinate lists, execution
fell from 3,000,201 to 905,426 emulator T-states, with identical state and output.
The speed/space tradeoff in that example is 201 extra assembled bytes.
`compile_source(source, optimize_graphics=False)` provides the unbatched path
for differential tests and inspecting the original code.

## Current subset

```python
int B = 3
i16 C = 4
int A = B + (C - 1)
A += 1
u8 brightness = 255
bool running = True
```

- Required declaration types: `int` aliases `i16`; `u8` and `bool` occupy one byte.
- Variables, assignment, `+=`, `-=`, addition/subtraction, remainder `%`, unary signs, parentheses.
- Comparisons `>`, `<`, `>=`, `<=`, `==`, `!=` produce bool values. Signed ordering
  uses the Z80 sign/overflow flags. Equality also works for bool operands.
- Indented `if`/`else` blocks, nesting, and `pass`. Conditions must be bool.
- `while` rechecks a bool condition before each iteration; `break` and `continue`
  target the innermost loop. Loop bodies may execute zero times, so declarations
  made only inside them are not considered initialized afterwards.
- Typed functions, positional calls, `return`, per-call parameters and locals,
  nested calls, recursion/mutual recursion, and `void` procedures are supported.
- Typed contiguous arrays are inferred from their initializer, e.g.
  `int scores = [10, 20, 30]`. `len(scores)` reads stored length in O(1).
- Function array parameters use `int[]`, `u8[]` or `bool[]` explicitly because
  parameters have no initializer to infer from. `for num in scores` infers
  the loop variable's scalar type from the array elements.
- Decimal/hex integer literals, comments, blank lines, spacing-independent syntax.
- Checks undeclared/duplicate names, bool/integer mixing and constant ranges.
- Dynamic i16-to-u8 narrowing is rejected until conversion/range rules are designed.
- Runtime arithmetic uses 16-bit Z80 operations. Overflow can wrap; this is a
  prototype limitation, **not a finalized TiPy overflow policy**.

Graphics builtins are next. This version emits assembly; it does not package `.8xp` files.
The browser at port 8084 now sends `.tip` projects to `POST /api/compile`. The
local Node server invokes `python -m tipy.web_compile`, which calls these Python
stages and returns assembly or a file/line/column error. There is no JavaScript
compiler fallback. Raw `.asm`/`.z80` entries go directly to the Z80 assembler.
Top-level `import utils` includes `utils.tip` from the sent in-memory files.
Graphics builtins from the earlier browser prototype are not supported by this
Python subset yet; saved sources are preserved and produce explicit errors.
The historical
`PyTie` workspace name and checked-out emulator directories have not been renamed.

## Verification

```sh
python -m unittest discover -s tipy/tests -v
```

The existing emulator also runs Python-generated assembly and checks the actual
values in RAM. From `asm-editor/ti84-runner`:

```powershell
$env:TIPY_PYTHON = 'C:/path/to/python.exe'
node build.mjs
node --test dist/python-compiler.test.mjs
```

To start the site with a specific Python interpreter, run from that same directory:

```powershell
$env:TIPY_PYTHON = 'C:/path/to/python.exe'
npm start
```

The executable defaults to `python` on PATH. The server binds to localhost port
8084; a missing interpreter is reported as a compiler-service error.

## Functions and calling convention

```python
def add(int x, int y) -> int:
    int result = x + y
    return result

int answer = add(3, 4)
```

Arguments are evaluated left-to-right and pushed as word copies, including byte
and bool arguments. CALL pushes the return address. The callee saves IX, uses it
as its frame pointer, and reserves space below it for locals. Arguments live at
positive IX offsets and locals at negative offsets. Early returns jump to one
epilogue that releases locals and restores IX. RET returns to the caller, which
removes argument words without overwriting the HL result. IY is preserved.
The compiler owns these details; TiPy programs do not manage registers or frames.

Functions are top-level and require explicit parameter and return types. `int`
resolves to `i16` in signatures. `void` functions can fall through or use a bare
`return`; value-returning functions must return on every checked path. Locals
are separate for each function and invocation and can shadow global names.
Implicit writes to globals and nested function definitions are not supported.
Global reads are checked against initialization at call sites, transitively.

This initial IX displacement layout supports up to 62 parameters and 128 bytes
of locals per function. Recursion uses available calculator RAM; there is no
stack-overflow guard yet. Full block scopes, closures, keyword/default arguments,
and proof that an infinite loop always returns are not implemented.

## Arrays

```python
int scores = [10, 20, 30]
scores.append(40)
scores.insert(1, 15)
int value = scores[0]
scores[-1] = 50
int last = scores.pop()
int size = len(scores)
```

The scalar type describes the elements; `[]` in the initializer establishes that
the variable is an array. `int scores = []` is an empty i16 array. `u8` and `bool`
arrays use one byte per element; i16 arrays use two. Types remain fixed after
declaration. Indexing is zero-based, supports negative indexes, and checks bounds.
Element assignments support `=`, `+=` and `-=` with the same scalar type rules.

Supported methods: `append(value)`, `insert(index, value)`, `pop()` / `pop(index)`,
`remove(value)` (first match), `clear()`, `extend(other)`, `reverse()`, `copy()`,
`count(value)`, and `index(value)` (first match). Insert indexes clamp like Python;
out-of-range reads/writes, empty pops and missing remove/index values are errors.

Each array has a stable, heap-allocated descriptor: data pointer, length,
capacity, element width and reference count, each a 16-bit word. The data block
is contiguous. Capacity grows geometrically when needed; growing copies data and
releases the old block. `len` is O(1); element access is O(1); insertion/removal,
search, copying, and reallocation can take O(n) work. Allocator scans also cost
time, so full append latency includes allocation work when capacity is exhausted.

Assignment aliases the descriptor, as with Python lists. `int other = scores`
shares mutations; `int other = scores.copy()` makes independent data. Reference
counts release replaced arrays and function locals on all normal return paths.
Clear releases the data block. A first-fit allocator with splitting and adjacent
free-block coalescing manages an 8 KiB arena included in the generated program.
The array runtime is generated Z80, not a host-only emulator implementation.
Only runtime routines referenced by the program and their transitive dependencies
are emitted. Instruction-level code generation is still deliberately unoptimized.

The current arena and capacity limit are fixed (maximum requested capacity 4096
elements); allocation may fail sooner due to element width, other arrays, growth
copy space or fragmentation. Allocation also checks a stack safety margin. Fatal
array errors unwind to the program entry stack and return with `__list_error`
set; the browser translates that code into an error message. TI-OS error dialogs
have not been added. Existing general recursion stack-overflow limits still apply.

Functions can return arrays with an explicit `-> int[]`, `-> u8[]`, or
`-> bool[]` annotation. `int result = function()` infers the returned array
type. Borrowed return values are retained before frame cleanup; temporary
results transfer their ownership to the caller. See `examples/get_evens.tip`.

Remainder `%` has higher precedence than `+` and `-` and follows Python's sign
rule (`-3 % 2 == 1`, `3 % -2 == -1`). Constant zero divisors are compile errors;
dynamic zero divisors stop execution with a runtime error. Positive power-of-two
literal divisors up to 256 use a bit mask; other divisors use a signed helper
with repeated unsigned subtraction, which is slower for large quotients.

This version supports one-dimensional arrays of scalars. Nested arrays,
slicing, comprehensions, sorting, and implicit array
arithmetic/comparison are not implemented. Arrays work as globals, per-call
locals, and typed parameters.

```python
def get_sum(int[] x) -> int:
    int total = 0
    for num in x:
        total += num
    return total

int x = [1, 2, 3, 4]
int total = get_sum(x)  # 10
```

Array parameters share the descriptor; mutations are visible to the caller.
Reassigning a parameter only replaces that call's reference. Calls retain their
array arguments and release them on return, including temporary arrays. A `for`
loop evaluates its iterable once, retains its descriptor for the duration, and
copies each element into the loop variable. Each iteration checks the current
length, so changes to length are observed like Python list iteration. Rebinding
the source variable does not change the object being iterated. `continue`
advances the index; `break` exits the innermost loop. Early function returns
release active iterators. A new loop variable is not definitely initialized
after the loop because the iterable may be empty.

These integration tests check nested subtraction, balanced stack use, assignment
without clobbering other variables, signed values, byte loads, bool copies and
distinct case-sensitive names.
