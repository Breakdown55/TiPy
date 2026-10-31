"""Resolve the shared GX/Easy GX model state before graphics lowering."""

from pathlib import Path
from .lexer import tokenize
from .parser import parse
from .scene_data import walk


def prepare(tree):
    functions = {
        node["name"]: node for node in tree["body"] if node["kind"] in {"function", "class"}
    }
    active = [
        node for node in tree["body"] if node["kind"] not in {"function", "class"}
    ]
    pending = {
        node["name"] for node in walk(active) if node.get("kind") in {"call", "name"}
    }
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        active.append(functions[name])
        pending.update(
            node["name"]
            for node in walk(functions[name])
            if node.get("kind") in {"call", "name"}
        )

    transforms = any(
        node.get("shared_graphics")
        and (
            node.get("name", "").startswith("__tipy_matrix_")
            or node.get("name")
            in {"__tipy_native_model_push", "__tipy_native_model_pop"}
        )
        for node in walk(active)
    )
    if not transforms:
        return
    from .errors import CompileError

    for node in walk(tree["body"]):
        if (
            node.get("shared_graphics")
            and node.get("name") == "__tipy_matrix_scale"
            and len(node["arguments"]) != 1
        ):
            raise CompileError("GX scale takes one uniform factor", node)
    tree["shared_model"] = True
    replacements = {
        "pixel_on": "point_on",
        "pixel_off": "point_off",
        "flip_pixel": "point_flip",
        "__tipy_toolkit_line": "line",
        "__tipy_toolkit_rect": "rect",
        "__tipy_toolkit_circle": "circle",
        "h_line": "line",
        "v_line": "line",
        "rectangle": "corner_rect",
    }
    for node in walk(tree["body"]):
        if node.get("shared_graphics") and node.get("kind") == "call":
            if node.get("name") in replacements:
                node["name"] = "__egx_" + replacements[node["name"]]
                if node["name"] in {"__egx_rect", "__egx_circle"}:
                    node["name"] = (
                        "__tipy_native_model_" + node["name"][len("__egx_") :]
                    )
            elif node.get("name") == "line" and len(node["arguments"]) == 4:
                node["name"] = "__egx_line"

    helpers = parse(
        tokenize(Path(__file__).with_suffix(".tip").read_text(encoding="utf-8"))
    )
    functions = {node["name"]: node for node in helpers["body"]}
    needed = set()
    pending = {
        node["name"] for node in walk(tree["body"]) if node.get("kind") == "call"
    }
    while pending:
        name = pending.pop()
        if name in needed or name not in functions:
            continue
        needed.add(name)
        pending.update(
            node["name"] for node in walk(functions[name]) if node.get("kind") == "call"
        )
    selected = [node for node in helpers["body"] if node["name"] in needed]
    for node in walk(selected):
        # Internal raster calls already receive transformed screen coordinates.
        if node.get("kind") == "call" and node.get("name") in {
            "line",
            "pixel_on",
            "pixel_off",
            "flip_pixel",
        }:
            node["raw_graphics"] = True
    tree["body"] = selected + tree["body"]
