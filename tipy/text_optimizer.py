"""Find bounded formatted prints that can avoid managed string temporaries."""

from .optimization_pipeline import walk


PURE_SCALAR_KINDS = {"name", "number", "boolean", "add", "subtract", "multiply",
                     "floor_divide", "modulo", "negate", "not", "bit_and", "bit_or",
                     "bit_xor", "shift_left", "shift_right", "compare", "compare_chain",
                     "and", "or"}


def pure_scalar(expression):
    return all(n.get("kind") in PURE_SCALAR_KINDS and n.get("type", "i16") in {"i16", "u8", "bool"}
               for n in walk(expression))


def stack_format_value(value):
    """Return (parts, capacity) for a bounded scalar f-string or concatenation.

    Keep general formatting on the managed path. Pure scalar expressions make
    this buffer a leaf temporary, so it fits within the native scratch reserve.
    The complete text is assembled before drawing, preserving tabs/newlines.
    """
    if value.get("kind") not in {"fstring", "sequence_binary"}:
        return None
    if any(node.get("evaluations") for node in walk(value)):
        return None
    def flatten(item):
        if item["kind"] == "fstring":
            return [part for value in item["values"] for part in flatten(value)]
        if item["kind"] == "sequence_binary" and item["name"] == "concat" and item["type"] == "str":
            return flatten(item["left"]) + flatten(item["right"])
        return [item]
    parts, capacity = [], 0
    for part in flatten(value):
        if part["kind"] == "string":
            parts.append(("literal", part["value"]))
            capacity += len(part["value"])
        elif (part["kind"] == "string_builtin" and part["name"] in {"number", "boolean"}
              and len(part["arguments"]) == 1 and pure_scalar(part["arguments"][0])):
            parts.append((part["name"], part["arguments"][0]))
            capacity += 5 if part["name"] == "boolean" else 6
        else:
            return None
    if not 0 < capacity <= 128:
        return None
    return parts, (capacity + 1) & ~1


def stack_format_parts(node):
    """Only print's pure coordinates may be evaluated with a stack buffer live."""
    if node.get("kind") != "text_print":
        return None
    value, x, y = node["arguments"]
    return stack_format_value(value) if pure_scalar(x) and pure_scalar(y) else None


def borrowed_concat_parts(value):
    """Fuse pure immutable-string chains without changing effect evaluation."""
    if value.get("kind") != "sequence_binary" or value.get("name") != "concat" or value.get("type") != "str":
        return None
    if any(node.get("evaluations") for node in walk(value)):
        return None
    def flatten(node):
        if node["kind"] == "sequence_binary" and node["name"] == "concat" and node["type"] == "str":
            return flatten(node["left"]) + flatten(node["right"])
        return [node]
    parts = flatten(value)
    if len(parts) > 32 or any(part["kind"] not in {"string", "name"} or part.get("cell") for part in parts):
        return None
    return parts
