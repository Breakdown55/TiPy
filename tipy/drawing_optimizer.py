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
        if item.get("kind") not in {"name", "number", "boolean", "add", "subtract", "negate", "compare", "and", "or", "not", "modulo", "multiply", "floor_divide"}:
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
            if len(values) == 3 and values[2] == 0: return None
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
                if item["kind"] != "for" or item["iteration_kind"] != "array" or len(item["body"]) != 1:
                    return False
                source, statement = item["sources"][0], item["body"][0]
                if source["kind"] != "name" or statement["kind"] != "expression_statement":
                    return False
                call = statement["value"]
                if call["kind"] != "graphics" or call["name"] not in {"pixel_on", "pixel_off", "flip_pixel"}:
                    return False
                x, y = call["arguments"]
                if x["kind"] != "name" or y["kind"] != "name" or key(x) != key(item["target"]) or key(y) != key(row) or key(x) == key(y):
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
                unique = {(key(source), mode == "flip_pixel") for _, source, mode in found}
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
                                slot = {"label": label, "symbol": label, "storage": "frame", "offset": -function["frame_size"], "type": type_name, "size": size}
                                function["locals"]["$" + label] = slot
                            else:
                                slot = {"label": label, "symbol": label, "type": type_name, "size": size}
                                if size == 12:
                                    tree.setdefault("drawing_buffers", []).append(slot)
                                else:
                                    tree["temporaries"][label] = slot
                            return slot
                        mask = {"buffer": allocate("", 12, "row_mask"), "last": allocate("_last", 2, source["type"][:-2]), "length": allocate("_length", 2, "i16")}
                        cached[cache_key] = mask
                        result.append({"kind": "prepare_row_mask", "source": source, "mask": mask, "parity": mode == "flip_pixel", "line": inner["line"]})
                    inner.update(kind="pixel_row", row=item["target"], mask=cached[cache_key], mode=mode)
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
