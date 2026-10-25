# UI reference

2026-10-24 follow-up: viewed user-attached
`C:/Users/XBbur/AppData/Local/Temp/codex-clipboard-11edf4a8-a19e-46f0-acf4-5831926c9e0d.png`.
It shows a large white screen with a thin black boundary and two small black
pixels near the upper-left corner. The user's requested override is a red
boundary around the drawable screen itself. Added an exterior 2px red CSS
outline to the canvas, preserving all 96x64 display pixels and the surrounding
black calculator border. Compile should preserve the selected tab, with Z80
output still available by clicking its tab; progress text is `Compiling…`.

User-authorized reference: [UI.png](UI.png), viewed 2026-10-24.

The sketch shows a plain white interface with black outlines. A narrow file-system
column sits left of the editor. File tabs (`main.pti`, `utils.pti`) are above the
editor. Compile sits at the bottom left of the editor. A separate tall panel at
right places the screen above a boxed button area. The red X is an annotation
over the editor, not a control to reproduce.

User addition: Compile must also show generated Z80 assembly. Implement as a
generated read-only tab, leaving source tabs accessible. User explicitly requests
a simple UI as well as simple controls.

Prior implementation: decorative header, dark editor, styled calculator shell,
extra inspector and controls. Revision: remove those; use file list, tabs, plain
editor, bottom Compile button, screen and keypad. Keep small status text for errors
and a Pause control while a program is active. The screenshot is a layout sketch;
functional file names, code and keypad replace the explanatory placeholder text.

Verified revision, 2026-10-24: viewed desktop source and compiled-output captures
against UI.png, plus the mobile capture. File column, tabs, bottom Compile, right
screen and boxed keypad are present; decorative header, shell and inspector are
removed. Compile opens the additional Z80 output tab as requested. Desktop gap
and panel sizes are responsive approximations of the sketch, not pixel-exact.

Follow-up reference: user-attached clipboard image
`C:/Users/XBbur/AppData/Local/Temp/codex-clipboard-500b9e10-1f97-487f-b92e-2773cbf59f36.png`,
viewed 2026-10-24. The narrow crop shows two dark tabs with a muted inactive label,
close buttons and a small ENTRY pill on one tab. User requests retaining our current
colors, changing TiPy source extension to `.tip`, and choosing an entry from the
left file list. Implemented muted gray inactive names in both tabs and list,
a blue ENTRY pill matching our accent, and a separate circle entry selector.
Viewed `entry-file-preview.png` to verify the light palette, dim inactive main.tip,
active utils.tip and ENTRY marker. Browser checks verify compiling the chosen
entry despite another tab being active and preserving entry selection on reload.
