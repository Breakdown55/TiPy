"""Present captured native LCD pixels at integer scale, with measured timings."""
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
captures = root/'asm-editor/ti84-runner/dist'
destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
results = json.loads((captures/'quality-results.json').read_text())
font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 20)
small = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 16)
sheet = Image.new('RGB', (1216, 664), '#202428')
draw = ImageDraw.Draw(sheet)
for index, result in enumerate(results):
    lcd = Image.open(captures/f'quality-{index}.pgm').convert('RGB')
    scaled = lcd.resize((384, 256), Image.Resampling.NEAREST)
    scaled.save(destination/f'quality-{index+1}.png')
    x = 16+(index % 3)*400
    y = 16+(index // 3)*312
    draw.text((x, y), f"{index+1}. {result['name']}", font=font, fill='white')
    draw.text((x, y+28), f"{result['fps']:.2f} FPS | {result['ms']:.1f} ms/frame", font=small, fill='#c2d2db')
    sheet.paste(scaled, (x, y+52))
draw.multiline_text((816, 348), 'Native Z80 at 15 MHz\nSame camera poses and game\nHUD + input + lighting included\nStartup excluded\n\n30 FPS target: 33.3 ms/frame', font=small, fill='#c2d2db', spacing=9)
sheet.save(destination/'graphics-quality-comparison.png')
(destination/'graphics-quality-results.json').write_text(json.dumps(results, indent=2))
print(destination/'graphics-quality-comparison.png')
