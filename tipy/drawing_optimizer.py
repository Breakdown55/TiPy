"""Batch invariant horizontal pixel loops into reusable 12-byte row masks.

This is a loop-idiom optimization, not a checkerboard special case. Only pure
regions qualify: no source mutation, function calls, early exits or rendering
may occur between the pixel writes. The original loop targets remain observable.
"""

from copy import deepcopy


def optimize_drawing(tree):
    tree = deepcopy(tree)
    serial = 0

    def pure(item):
        if not isinstance(item, dict):
            return True
        if item.get("kind") not in {
            "name",
            "number",
            "boolean",
            "add",
            "subtract",
            "negate",
            "compare",
            "and",
            "or",
            "not",
            "modulo",
            "multiply",
            "floor_divide",
        }:
            return False
        return all(pure(value) for value in item.values() if isinstance(value, dict))

    def key(slot):
        return slot.get("storage", "global"), slot.get("symbol"), slot.get("offset")

    def constant(item):
        if item["kind"] == "number":
            return item["value"]
        if item["kind"] == "negate":
            value = constant(item["value"])
            return -value if value is not None else None
        return None

    def candidate(loop):
        row = loop["target"]
        if loop["iteration_kind"] == "range":
            if not all(pure(arg) for arg in loop["iterable"]["arguments"]):
                return None
            values = [constant(arg) for arg in loop["iterable"]["arguments"]]
            if len(values) == 3 and values[2] == 0:
                return None
            if all(value is not None for value in values) and len(range(*values)) < 4:
                return None  # Mask construction does not amortize for tiny loops.
        elif loop["iteration_kind"] != "array" or loop["sources"][0]["kind"] != "name":
            return None
        found = []

        def inspect(body):
            for item in body:
                if item.get("unreachable") or item["kind"] == "pass":
                    continue
                if item["kind"] == "if" and pure(item["condition"]):
                    if not inspect(item["body"]) or not inspect(item["alternative"]):
                        return False
                    continue
                if (
                    item["kind"] != "for"
                    or item["iteration_kind"] != "array"
                    or len(item["body"]) != 1
                ):
                    return False
                source, statement = item["sources"][0], item["body"][0]
                if (
                    source["kind"] != "name"
                    or statement["kind"] != "expression_statement"
                ):
                    return False
                call = statement["value"]
                if call["kind"] != "graphics" or call["name"] not in {
                    "pixel_on",
                    "pixel_off",
                    "flip_pixel",
                }:
                    return False
                x, y = call["arguments"]
                if (
                    x["kind"] != "name"
                    or y["kind"] != "name"
                    or key(x) != key(item["target"])
                    or key(y) != key(row)
                    or key(x) == key(y)
                ):
                    return False
                found.append((item, source, call["name"]))
            return True

        return found if inspect(loop["body"]) and found else None

    def transform(body, function=None):
        nonlocal serial
        result = []
        for item in body:
            if item.get("unreachable"):
                result.append(item)
                continue
            found = candidate(item) if item["kind"] == "for" else None
            if found:
                unique = {
                    (key(source), mode == "flip_pixel") for _, source, mode in found
                }
                if function and function["frame_size"] + 16 * len(unique) > 128:
                    found = None
            if found:
                cached = {}
                for inner, source, mode in found:
                    cache_key = key(source), mode == "flip_pixel"
                    if cache_key not in cached:
                        serial += 1
                        name = f"__row_mask_{serial}"

                        def allocate(suffix, size, type_name):
                            label = name + suffix
                            if function:
                                function["frame_size"] += size
                                slot = {
                                    "label": label,
                                    "symbol": label,
                                    "storage": "frame",
                                    "offset": -function["frame_size"],
                                    "type": type_name,
                                    "size": size,
                                }
                                function["locals"]["$" + label] = slot
                            else:
                                slot = {
                                    "label": label,
                                    "symbol": label,
                                    "type": type_name,
                                    "size": size,
                                }
                                if size == 12:
                                    tree.setdefault("drawing_buffers", []).append(slot)
                                else:
                                    tree["temporaries"][label] = slot
                            return slot

                        mask = {
                            "buffer": allocate("", 12, "row_mask"),
                            "last": allocate("_last", 2, source["type"][:-2]),
                            "length": allocate("_length", 2, "i16"),
                        }
                        cached[cache_key] = mask
                        result.append(
                            {
                                "kind": "prepare_row_mask",
                                "source": source,
                                "mask": mask,
                                "parity": mode == "flip_pixel",
                                "line": inner["line"],
                            }
                        )
                    inner.update(
                        kind="pixel_row",
                        row=item["target"],
                        mask=cached[cache_key],
                        mode=mode,
                    )
                result.append(item)
            else:
                if item["kind"] == "function":
                    item["body"] = transform(item["body"], item)
                elif item["kind"] in {"for", "while", "if"}:
                    item["body"] = transform(item["body"], function)
                    if item["kind"] == "if":
                        item["alternative"] = transform(item["alternative"], function)
                result.append(item)
        return result

    tree["body"] = transform(tree["body"])
    return tree


def annotate_blank_meshes(tree):
    """A clear reaches a draw only through operations proven not to draw."""
    from .optimization_pipeline import walk

    pure_functions = {
        f["label"]
        for f in tree["body"]
        if f.get("kind") == "function" and "finite_table" in f
    }
    non_drawing_nodes = {
        "expression_statement",
        "assign",
        "declare",
        "collect",
        "number",
        "boolean",
        "string",
        "name",
        "geometry_data",
        "address",
        "global_value",
        "void_constant",
        "constant_list",
        "add",
        "subtract",
        "multiply",
        "floor_divide",
        "modulo",
        "compare",
        "and",
        "or",
        "not",
        "negate",
        "invert",
        "bit_and",
        "bit_or",
        "bit_xor",
        "shift_left",
        "shift_right",
        "power",
        "list",
        "tuple",
        "dict",
        "set",
        "index",
        "length",
        "key_down",
        "native",
        "builtin",
    }
    for node in walk(tree):
        if node.get("kind") == "native_graphics" and node.get("name") == "mesh_draw":
            node["blank_framebuffer"] = False

    def visit_expression(node, is_blank):
        if isinstance(node, list):
            for value in node:
                is_blank = visit_expression(value, is_blank)
            return is_blank
        if not isinstance(node, dict):
            return is_blank
        if node.get("kind") == "conditional":
            state = visit_expression(node["condition"], is_blank)
            yes = visit_expression(node["body"], state)
            no = visit_expression(node["alternative"], state)
            return yes and no
        for key, value in node.items():
            if key not in {"blank_framebuffer", "body", "alternative", "final"}:
                is_blank = visit_expression(value, is_blank)
        kind = node.get("kind")
        if kind == "call" and node.get("symbol") not in pure_functions:
            return False
        if kind in {
            "closure_call",
            "text_print",
            "text_input",
            "pixel_batch",
            "native_matrix",
        }:
            return False
        if kind == "graphics":
            if node["name"] == "clear":
                return True
            if node["name"] == "render":
                return is_blank
            return False
        if kind == "native_graphics":
            if node["name"] in {"mesh_shadow", "mesh_buffer_write"}:
                return False
            if node["name"] == "mesh_draw":
                arg = node["arguments"][12]
                if arg.get("kind") == "name":
                    arg = next(
                        (
                            e["value"]
                            for e in node.get("evaluations", [])
                            if e["slot"]["label"] == arg.get("label")
                        ),
                        arg,
                    )
                node["blank_framebuffer"] = bool(
                    is_blank and arg.get("kind") == "boolean" and arg["value"]
                )
                return False
            if not node["name"].startswith(("mesh_", "solid_")):
                return False
        if kind is not None and kind not in non_drawing_nodes | {
            "call",
            "native_graphics",
            "graphics",
        }:
            return False
        return is_blank

    def visit_statements(body, is_blank):
        for node in body:
            if node.get("unreachable"):
                continue
            kind = node.get("kind")
            if kind == "function":
                visit_statements(node["body"], False)
                continue
            if kind == "if":
                is_blank = visit_expression(node["condition"], is_blank)
                yes = visit_statements(node["body"], is_blank)
                no = visit_statements(node["alternative"], is_blank)
                is_blank = yes and no
            elif kind in {"while", "for"}:
                visit_statements(node["body"], False)
                is_blank = False
            elif isinstance(node.get("body"), list):
                visit_statements(node["body"], False)
                is_blank = False
            elif kind in {"return", "break", "continue", "raise"}:
                return False
            else:
                is_blank = visit_expression(node, is_blank)
        return is_blank

    visit_statements(tree["body"], False)


def annotate_legacy_lighting(tree):
    """Track whether the implicit default directional light can reach a draw."""
    pure_functions = {
        node["label"]
        for node in tree["body"]
        if node.get("kind") == "function" and "finite_table" in node
    }

    def expression(node, enabled):
        if isinstance(node, list):
            for child in node:
                enabled = expression(child, enabled)
            return enabled
        if not isinstance(node, dict):
            return enabled
        if node.get("kind") == "conditional":
            state = expression(node["condition"], enabled)
            yes = expression(node["body"], state)
            no = expression(node["alternative"], state)
            return yes or no
        for key, child in node.items():
            if key not in {"body", "alternative", "final", "legacy_lighting"}:
                enabled = expression(child, enabled)
        if (
            node.get("kind") in {"call", "closure_call"}
            and node.get("symbol") not in pure_functions
        ):
            return True
        if node.get("kind") == "native_graphics":
            name = node["name"]
            if name in {
                "solid_no_lights",
                "solid_directional_light",
                "solid_point_light",
            }:
                return False
            if name == "solid_light":
                return True
            if name == "mesh_draw":
                node["legacy_lighting"] = enabled
        return enabled

    def statements(body, enabled):
        for node in body:
            if node.get("unreachable"):
                continue
            kind = node.get("kind")
            if kind == "function":
                statements(node["body"], True)
            elif kind == "if":
                enabled = expression(node["condition"], enabled)
                yes = statements(node["body"], enabled)
                no = statements(node["alternative"], enabled)
                enabled = yes or no
            elif isinstance(node.get("body"), list):
                statements(node["body"], True)
                enabled = True
            elif kind in {"return", "break", "continue", "raise"}:
                return True
            else:
                enabled = expression(node, enabled)
        return enabled

    statements(tree["body"], True)
