"""Explicitly imported TiPy source modules, with project files taking priority."""

from pathlib import Path

MODULES = {name + ".tip" for name in ("motion", "geometry", "timing", "lighting")}


def module_source(name):
    if name not in MODULES:
        return None
    return (Path(__file__).with_name("lib") / name).read_text(encoding="utf-8")
