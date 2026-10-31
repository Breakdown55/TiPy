"""Render the measured Z80 circle comparison as a standalone HTML report."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
folder = root / "asm-editor/ti84-runner/dist"
rows = json.loads((folder / "circle-benchmark.json").read_text(encoding="utf-8"))
names = {"original": "Original", "cached": "Exact cached", "bresenham": "Bresenham", "current": "Adaptive default"}
cards = []
for variant in dict.fromkeys(row["variant"] for row in rows):
    row = next(item for item in rows if item["variant"] == variant)
    pixels = []
    for index, value in enumerate(row["pixels"]):
        x, y = index % 96 - 30, index // 96 + 5
        if value and 0 <= x < 32 and 0 <= y < 32:
            pixels.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    difference = next(item for item in row["accuracy"] if item["radius"] == 10)["differingPixels"]
    cards.append(f'''<section><h2>{names[variant]}</h2>
<svg viewBox="0 0 32 32" role="img" aria-label="Native circle pixels"><rect width="32" height="32" fill="white"/>
<g fill="#111">{''.join(pixels)}</g><circle cx="15.5" cy="15.5" r="10" fill="none" stroke="#1261a0" stroke-width=".09"/></svg>
<dl><dt>6 MHz</dt><dd>{row['fps6']:.1f} FPS</dd><dt>15 MHz</dt><dd>{row['fps15']:.1f} FPS</dd>
<dt>Mean radial error</dt><dd>{row['radialMeanError']:.3f} px</dd><dt>Maximum radial error</dt><dd>{row['radialMaxError']:.3f} px</dd>
<dt>Changed pixels</dt><dd>{difference}</dd><dt>Machine bytes</dt><dd>{row['bytes']:,}</dd></dl></section>''')
html = f'''<!doctype html><meta charset="utf-8"><title>TiPy native circle benchmark</title>
<style>body{{font:15px 'Segoe UI',sans-serif;max-width:980px;padding:24px;margin:auto;color:#17202b;background:#f5f6f8}}h1{{font-size:26px;margin:0 0 8px}}p{{line-height:1.5;color:#526071;margin:8px 0 18px}}main{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}section{{background:white;padding:18px;border:1px solid #d8dfe6;border-radius:8px}}h2{{margin:0 0 8px;font-size:19px}}svg{{width:100%;image-rendering:pixelated}}dl{{display:grid;grid-template-columns:1fr auto;gap:9px;font-size:13px}}dt{{color:#526071}}dd{{margin:0;font-weight:600}}footer{{font-size:12px;line-height:1.5;margin-top:20px;color:#526071}}</style>
<h1>TiPy: original vs. Bresenham circle</h1><p>Native Z80 execution · moving 20-pixel-diameter circle · same clear, input and render loop</p>
<main>{''.join(cards)}</main><footer>FPS is calculated from measured steady-state Z80 T-states, including drawing, input, clear and render. Display refresh can cap visible FPS. Blue line: ideal radius-10 circle through pixel-center coordinates. Radial error measures black pixel centers against that circle; lower is better. Changed pixels compares against the original outline, not mathematical accuracy. First-frame cache setup is excluded from steady-state FPS; it is recorded in the JSON data.</footer>'''
(folder / "circle-benchmark.html").write_text(html, encoding="utf-8")
print(folder / "circle-benchmark.html")
