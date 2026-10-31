"""Generate editor entries from the compiler's actual native module exports."""
import json
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from tipy.stdlib import MEMBERS
from tipy.toolkit_namespace import ALIASES
from tipy.camera_api import INTRINSICS
from tipy.checker import GRAPHICS_BUILTINS
documentation = json.loads((root / "vscode-extension/src/documentation.json").read_text(encoding="utf-8"))

signatures = {}
for file in (root / "tipy").rglob("*.tip"):
    if "tests" in file.parts:
        continue
    for match in re.finditer(r"^def (\w+)\(([^\n]*)\)(?:\s*->\s*([^:]+))?:", file.read_text(encoding="utf-8"), re.M):
        signatures[match[1]] = match[2]
for name, (params, types, result, defaults) in INTRINSICS.items():
    if isinstance(defaults, list):
        defaults = dict(zip(params, defaults))
    signatures["__tipy_native_" + name] = ", ".join(
        f"{typ} {param}" + ("=" + repr(defaults[param]) if defaults and param in defaults else "")
        for param, typ in zip(params, types))

common = {
    "clear": "", "render": "", "pixel_on": "int x, int y", "pixel_off": "int x, int y",
    "flip_pixel": "int x, int y", "key_down": "str key", "print": "value, int x, int y",
    "input": "str prompt, int x, int y", "line": "int x1, int y1, int x2, int y2",
    "h_line": "int x1, int y1, int x2, int y2", "v_line": "int x1, int y1, int x2, int y2",
    "rectangle": "int x1, int x2, int y1, int y2, int width, bool filled",
    "set_cpu_speed": "int mhz", "set_render_quality": "str quality",
    "len": "value", "range": "int stop", "list": "iterable", "int": "value", "str": "value",
    "bool": "value", "float": "value", "abs": "value", "min": "a, b", "max": "a, b",
    "sum": "values", "any": "values", "all": "values", "enumerate": "values", "zip": "a, b",
    "sorted": "values", "reversed": "values", "round": "value", "pow": "base, exponent",
    "divmod": "a, b", "tuple": "values", "dict": "", "set": "values", "iter": "values",
    "next": "iterator", "map": "function, values", "filter": "function, values",
    "time.sleep": "float seconds", "time.frame_ticks": "", "time.stop_frame_timer": "",
    "random.randint": "int low, int high", "random.seed": "int value",
    "math.radians": "float degrees", "math.sin": "float radians", "math.cos": "float radians",
}
signatures.update(common)
for name, params in list(signatures.items()):
    if name.startswith("__tipy_gfx_"):
        signatures.setdefault(name[len("__tipy_gfx_"):], params)
for public, native in {"init_camera": "mesh_init", "camera": "mesh_look", "set_fov": "mesh_fov",
                       "draw_mesh": "mesh_draw", "draw_shadow": "mesh_shadow", "collides": "mesh_collides",
                       "no_lights": "solid_no_lights", "ambient_light": "solid_ambient_light",
                       "point_light": "solid_point_light", "directional_light": "solid_directional_light"}.items():
    if "__tipy_native_" + native in signatures:
        signatures[public] = signatures["__tipy_native_" + native]
signatures.update({
    "__gx_vertex": "int x, int y, int z=...",
    "__gx_draw": "int mode, vertices, indices=None, int dimensions=2",
    "__gx_load_matrix": ', '.join(f'float m{row}{column}' for row in range(4) for column in range(4)),
    "clear_rect": "int x, int y, int width, int height",
    "__tipy_toolkit_line": common["line"],
    "__tipy_toolkit_rect": "int x, int y, int width, int height, int thickness=1, bool filled=True",
    "__tipy_toolkit_circle": "int x, int y, int radius, bool filled=True",
    "__tipy_matrix_translate": "int x, int y, int z=0",
    "__tipy_matrix_scale": "float factor",
    **{"__tipy_matrix_" + name: "float radians" for name in ("rotate", "rotate_x", "rotate_y", "rotate_z")},
    "__tipy_matrix_reset_matrix": "",
    "load_obj": 'str filename, int radius=32, str object="", bool normalize=True, int brightness=16',
    "mesh": "vertices, edges=[], faces=[]",
    "collision_map": "str filename",
    "load_scene": "str filename",
    "draw_scene": "scene, shadow_light=None",
    "set_visible": "scene, str object, bool visible",
    "scene_collisions": "scene",
})
def entry(name, target=None, module=None):
    target = target or name
    args = signatures.get(target)
    description = documentation["functions"].get(f"{module}.{name}") or documentation["functions"].get(target, documentation["functions"].get(name))
    if not description:
        raise ValueError(f"Missing function documentation: {module or 'builtins'}.{name}")
    if module in {"gx", "egx"} and name in {"translate", "rotate", "rotate_x", "rotate_y", "rotate_z", "scale", "push_matrix", "pop_matrix", "reset_matrix"}:
        if name == "scale":
            description = "Scales subsequent geometry by one positive uniform factor. GX and EGX share this transform; use push_matrix() and pop_matrix() to restore the previous scale."
        else:
            description += " GX and EGX share the same geometry transform and stack."
    return {"name": name, "kind": "constant" if name.isupper() else "function", "params": args, "signature": name + ("(" + args + ")" if args is not None else ""),
            "description": description}

modules = {module: [entry(name, ALIASES.get(module, {}).get(name, name if module == "py5" else module + "." + name), module)
                    for name in sorted(names)] for module, names in MEMBERS.items()}
builtins = [entry(name) for name in sorted(set(common) | GRAPHICS_BUILTINS) if "." not in name]
helper_sources = {file.stem: file.read_text(encoding="utf-8") for file in (root / "tipy/lib").glob("*.tip")}
print(json.dumps({"modules": modules, "builtins": builtins, "containers": documentation["containers"],
                  "helpers": documentation["helpers"], "helperSources": helper_sources,
                  "moduleDescriptions": documentation["modules"]}))
