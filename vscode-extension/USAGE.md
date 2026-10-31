# TiPy for VS Code

Edit `.tip` programs, compile to native Z80, and run them in a ROM-free TI-84 Plus emulator inside VS Code.

## Install

Install Python 3.10 or newer. In VS Code, run **Extensions: Install from VSIX…** and choose `tipy-0.2.0.vsix`. Open a folder containing your `.tip` files. Compilation and execution require a trusted workspace.

If Python is not detected, set **TiPy: Python Path** to your Python executable. The compiler and emulator are bundled; no browser server or calculator ROM is needed.

## Edit, compile, run

- Open a `.tip` file. Highlighting, snippets, completions, hover information, and parameter hints are available. Type `egx.` after `import egx` to see supported members.
- Use **TiPy: Compile** or **Ctrl+Shift+B** (macOS: Cmd+Shift+B). Compile leaves your editor selected and reports machine bytes, code/data, and heap.
- Use **TiPy: Run in Emulator** or **Ctrl+Enter** (macOS: Cmd+Enter). Run compiles your latest edits and opens the calculator beside your code.
- Click the calculator screen for keyboard input. Arrows are the calculator arrows, Ctrl is 2nd, Shift is Alpha, and Escape is Clear. The expandable keypad supports mouse/touch input.
- Use Pause, Restart, Stop, or **TiPy: Stop Emulator**. Losing window focus pauses execution and releases held keys.
- Right-click a `.tip` file and choose **TiPy: Set as Entry File** to compile it while editing another file. Clear `tipy.entryFile` to return to compiling the active file.
- Use **TiPy: Show Generated Z80 Assembly** to inspect the last successful output in a read-only tab.

Unsaved edits in saved `.tip` and `.obj` files are included. An untitled file must be saved first. Imported modules and OBJ assets must be siblings of the entry file; the current compiler uses flat module names. Projects support up to 64 source/OBJ files and 1 MB of source. Bundled helper modules (`motion`, `geometry`, `timing`, `lighting`) remain available. Local helper files take precedence.

Diagnostics appear in Problems after edits. The checker reports the first compiler error per project; compiling also checks native code generation and the assembled program's memory limit. Completion is a lightweight editor provider, rather than a full Python language server; it suggests TiPy's supported APIs, imported helpers, local symbols, and common container methods. It does not promise CPython compatibility.

`gx` exposes explicit triangle/line submissions, shade, culling, depth testing,
matrices, and raw buffer access. `egx` provides convenient shapes, text, input,
OBJ models, lights, and scenes. Both share the framebuffer, model transform,
native camera, and depth buffer. Both display the combined frame with `render()`.
See the repository's `docs/gx.md` and `examples/gx_primitives.tip` for the full API
and a small mixed XY/XYZ drawing example.

## Settings

`tipy.pythonPath` selects Python. `tipy.compilerPath` can point to a TiPy source checkout containing `tipy/`; otherwise the bundled compiler is used. `tipy.optimization` selects reference, balanced, or aggressive compilation. `tipy.liveDiagnostics`, `tipy.diagnosticDelay`, and `tipy.compileTimeout` control background checking and compilation.

## Development

From `vscode-extension`, run `npm ci`, `npm run build`, and `npm test`. Run `npm run package` to produce `dist/tipy-0.2.0.vsix`. Packaging requires the repository's checked-out Z80 submodule and Python. To debug, launch VS Code with `--extensionDevelopmentPath` pointing to this directory, or press F5 using the included launch configuration.

TiPy is licensed under GPL-3.0-only. The bundled Z80 assembler/emulator includes MIT-licensed upstream code; see `THIRD-PARTY-NOTICES.txt`.
