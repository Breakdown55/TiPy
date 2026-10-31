"""Build a standalone report from measured native Z80 circle sweeps."""
import html
import json
from pathlib import Path

folder = Path(__file__).resolve().parents[1] / "asm-editor/ti84-runner/dist"
data = json.loads((folder / "circle-sweep.json").read_text(encoding="utf-8"))
names = {"original": "Original", "full_bresenham": "Full Bresenham", "arc": "Visible arcs", "adaptive": "Adaptive default"}
colors = dict(zip(names, ["#858b98", "#3975cb", "#d78a22", "#14845c"]))
plots = []
for pose in ("centered", "top-cap"):
    for filled in (False, True):
        paths = []
        for variant in names:
            rows = sorted((r for r in data["dense"] if r["variant"] == variant and r["pose"] == pose and r["filled"] == filled), key=lambda r: r["radius"])
            points = " ".join(f'{45 + r["radius"] * 3.25:.1f},{245 - min(r["fps6"], 100) * 2.1:.1f}' for r in rows)
            paths.append(f'<polyline points="{points}" fill="none" stroke="{colors[variant]}" stroke-width="{2.5 if variant == "adaptive" else 1.3}"/>')
        ticks = ''.join(f'<text x="32" y="{249-v*2.1}" text-anchor="end">{v}</text><path d="M40 {245-v*2.1}H470" stroke="#e5e8ed"/>' for v in (0,25,50,75,100))
        xticks = ''.join(f'<text x="{45+r*3.25}" y="265" text-anchor="middle">{r}</text>' for r in (0,32,64,96,128))
        plots.append(f'<section><h2>{pose.replace("-", " ").title()} · {"filled" if filled else "outline"}</h2><svg viewBox="0 0 490 290" role="img" aria-label="FPS versus circle radius">{ticks}{"".join(paths)}{xticks}<text x="255" y="286" text-anchor="middle">Radius (pixels)</text><text x="45" y="20">FPS at 6 MHz</text></svg></section>')

table = []
for radius in (256, 1024, 32767):
    for pose in ("centered", "top-cap", "left-arc"):
        cells = []
        for variant in names:
            pair = []
            for filled in (False, True):
                row = next(r for r in data["stress"] if r["variant"] == variant and r["radius"] == radius and r["pose"] == pose and r["filled"] == filled)
                pair.append(("&lt;" if not row["completed"] else "") + f'{row["fps6"]:.2f}')
            cells.append('<td>' + " / ".join(pair) + '</td>')
        table.append(f'<tr><td>{radius:,}</td><td>{html.escape(pose)}</td>{"".join(cells)}</tr>')
rows = data["dense"] + data["stress"]
checked = [r for r in rows if r.get("completed", True)]
assert all(r["pixelErrors"] == 0 for r in checked)
legend = ''.join(f'<span style="color:{colors[k]}">━ {v}</span>' for k,v in names.items())
report = f'''<!doctype html><meta charset="utf-8"><title>TiPy circle size sweep</title>
<style>body{{font:15px 'Segoe UI',sans-serif;background:#f5f6f8;color:#182332;max-width:1080px;margin:auto;padding:26px}}h1{{margin:0;font-size:28px}}p{{line-height:1.55;color:#526071}}.legend{{display:flex;gap:24px;margin:18px 0;font-weight:600}}main{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}section{{background:white;border:1px solid #dce1e8;border-radius:8px;padding:14px}}h2{{font-size:17px;margin:0}}svg{{width:100%;font-size:11px;fill:#526071}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:9px;text-align:right;border-bottom:1px solid #e5e8ed}}th:first-child,td:first-child{{text-align:left}}footer{{font-size:12px;line-height:1.6;color:#526071;margin-top:18px}}</style>
<h1>Circle rendering across sizes</h1><p>96 × 64 viewport · native Z80 execution · outline and filled primitives<br>Every radius 0–128, plus 256, 512, 1,024, 4,096, 16,384 and 32,767.</p>
<div class="legend">{legend}</div><main>{''.join(plots)}</main>
<h2 style="margin:24px 0 12px">Large-radius stress cases: outline / filled FPS at 6 MHz</h2>
<table><tr><th>Radius</th><th>Placement</th>{''.join(f'<th>{n}</th>' for n in names.values())}</tr>{''.join(table)}</table>
<footer>{len(rows):,} measured cases across four variants; {len(checked):,} completed cases match their independent pixel references exactly. The original uses an inside-disk convention; all other variants use the same midpoint/Bresenham convention. Full walks exceeding 40 million T-states are stopped: their FPS is an upper bound, marked &lt;.<br>
Each timing includes one circle call, clear and render. The dense sweep also includes loop/dispatch overhead; stress cases are standalone programs. These are cycle-derived rates, not physical hardware or browser refresh measurements. At 15 MHz, multiply by 2.5. The user's moving circle performs both white fill and black outline, and has a separate frame benchmark.<br>
Top-cap: center (48, 8 − radius); left-arc: center (8 − radius, 32). Adaptive routing uses full-viewport fill/rejection, short Bresenham walks, or scans the shorter visible row/column interval. It is a measured cost heuristic, not a guarantee of the fastest choice at every radius.</footer>'''
(folder / "circle-sweep.html").write_text(report, encoding="utf-8")
print(folder / "circle-sweep.html")
