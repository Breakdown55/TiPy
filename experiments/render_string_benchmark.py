"""Report native string workloads before/after optimization.

From asm-editor/ti84-runner: node build.mjs, then set
TIPY_STRING_BENCHMARK=1 and TIPY_STRING_BENCHMARK_LABEL=before (or after),
and run node --test --test-name-pattern='String operation benchmark'
dist/python-compiler.test.mjs. Capture 'before' with the previous compiler.
Run this script from any directory to render both saved measurement files.
"""
import json
from pathlib import Path

folder = Path(__file__).resolve().parents[1] / "asm-editor/ti84-runner/dist"
before = {row["name"]: row for row in json.loads((folder / "string-benchmark-before.json").read_text())}
after = json.loads((folder / "string-benchmark-after.json").read_text())
names = {"fstring": "Stored f-string", "join": "Join four strings", "upper": "Uppercase",
         "lower": "Lowercase", "find": "Substring search", "startswith": "Prefix test",
         "endswith": "Suffix test", "repeat": "String repetition", "slice": "Contiguous slice",
         "concat": "Concatenation chain"}
rows = []
for row in after:
    old = before[row["name"]]
    ratio = old["tStates"] / row["tStates"]
    delta = row["bytes"] - old["bytes"]
    rows.append(f'<tr><td>{names[row["name"]]}</td><td>{old["tStates"]:,}</td><td>{row["tStates"]:,}</td>'
                f'<td><b>{ratio:.2f}×</b></td><td>{delta:+,}</td></tr>')
report = f'''<!doctype html><meta charset="utf-8"><title>TiPy native string optimizations</title>
<style>body{{font:16px 'Segoe UI',sans-serif;color:#192633;background:#f5f6f8;max-width:1000px;padding:30px;margin:auto}}h1{{font-size:28px}}p{{line-height:1.6;color:#526071}}table{{width:100%;border-collapse:collapse;background:white;font-size:14px}}td,th{{padding:14px;text-align:right;border-bottom:1px solid #e1e6ec}}td:first-child,th:first-child{{text-align:left}}b{{color:#137151}}footer{{font-size:13px;line-height:1.6;color:#526071;margin-top:22px}}</style>
<h1>TiPy native string optimizations</h1><p>Before: compiler bundled in extension 0.2.8 · After: optimized string compiler/runtime<br>Actual generated Z80 executed in the emulator; lower T-states is better.</p>
<table><tr><th>Workload</th><th>Before T-states</th><th>After T-states</th><th>Speedup</th><th>Image byte change</th></tr>{''.join(rows)}</table>
<footer>Each workload executes its string operation 20 times. Timings include setup, loop overhead, allocation and cleanup; they are not isolated instruction counts. Reference compilation keeps the work in the program. Every result is checked. Ratios apply to these inputs and vary with string length, contents and allocation state. The workloads and full generated assembly are saved in the adjacent JSON files.<br>
Stored bounded scalar formatting builds a stack buffer and allocates only the final string. Pure string concatenation chains allocate once; expressions with side effects retain ordered evaluation. Join measures final capacity before copying. Case conversion uses native byte loops. Contiguous byte slices use block copies; stepped slices retain the general implementation. Repetition doubles already-copied blocks. Search scans bytes natively, while prefix/suffix tests compare only the requested span.<br>
Unused native helpers remain excluded from the program. The contiguous-slice branch costs 132 image bytes in this workload; substring search costs 87. Other measured images shrink.</footer>'''
(folder / "string-benchmark.html").write_text(report, encoding="utf-8")
print(folder / "string-benchmark.html")
