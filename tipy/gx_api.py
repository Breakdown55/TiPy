"""Lower explicit primitive submissions into native, bounded GX operations."""
from copy import deepcopy
from .errors import CompileError
from .scene_data import arguments, fragment, walk


def install(tree):
    serial = 0

    def integer(node):
        if node.get("kind") == "number": return node["value"]
        if node.get("kind") == "negate":
            value = integer(node["value"])
            return -value if value is not None else None
        return None

    def call(name, values, origin):
        return {"kind": "expression_statement", "value": {
            "kind": "call", "name": name, "arguments": values, "shared_graphics": True,
            **{k: origin[k] for k in ("line", "column", "filename") if k in origin}},
            **{k: origin[k] for k in ("line", "column", "filename") if k in origin}}

    def submit(node):
        nonlocal serial
        values = arguments(node, ["mode", "vertices", "indices", "dimensions"], {
            "indices": {"kind": "none"}, "dimensions": {"kind": "number", "value": 2}})
        dimensions = integer(values["dimensions"])
        if dimensions not in (2, 3):
            raise CompileError("GX draw dimensions must be the constant 2 or 3", node)
        rows, indices, mode = values["vertices"], values["indices"], integer(values["mode"])
        if mode is not None and mode not in (0, 1):
            raise CompileError("Use gx.TRIANGLES or gx.LINES", node)
        # Constant buffers become direct vertex submissions without building
        # Python-style containers or including a heap in the native program.
        if rows.get("kind") in {"list", "tuple"} and all(
            row.get("kind") in {"list", "tuple"} and len(row["values"]) == dimensions
            and all(integer(value) is not None for value in row["values"]) for row in rows["values"]
        ) and (indices.get("kind") == "none" or indices.get("kind") in {"list", "tuple"}
               and all(integer(value) is not None for value in indices["values"])):
            order = list(range(len(rows["values"]))) if indices["kind"] == "none" else [integer(value) for value in indices["values"]]
            if any(not 0 <= index < len(rows["values"]) for index in order):
                raise CompileError("GX index is outside the vertex buffer", node)
            if mode is not None and len(order) % (3 if mode == 0 else 2):
                raise CompileError("GX draw requires complete triangles or line pairs", node)
            return [call("__tipy_native_gx_begin", [values["mode"]], node),
                    *[call(f"__tipy_native_gx_vertex{dimensions}", deepcopy(rows["values"][index]["values"]), node) for index in order],
                    call("__tipy_native_gx_end", [], node)]
        serial += 1
        prefix = f"__gx_submit_{serial}_"
        result = []
        # Evaluate supplied arguments once in source order, including keywords.
        positional = iter(("mode", "vertices", "indices", "dimensions"))
        for arg in node["arguments"]:
            key, value = (arg["name"], arg["value"]) if arg.get("kind") == "keyword" else (next(positional), arg)
            if key == "dimensions" or key == "indices" and value.get("kind") == "none": continue
            declaration = fragment(f"{prefix}{key} = 0", node)[0]
            declaration["value"] = value
            result.append(declaration)
        indexed = indices.get("kind") != "none"
        vertices = prefix + "vertices"
        sequence = prefix + "indices" if indexed else vertices
        access = f"{vertices}[{sequence}[{prefix}i]]" if indexed else f"{vertices}[{prefix}i]"
        source = f"""__tipy_native_gx_begin({prefix}mode)
for {prefix}i in range(len({sequence})):
    {prefix}point = {access}
    __tipy_native_gx_vertex{dimensions}({','.join(f'{prefix}point[{i}]' for i in range(dimensions))})
__tipy_native_gx_end()
"""
        result.extend(fragment(source, node))
        return result

    def statements(items):
        nonlocal serial
        output = []
        for item in items:
            value = item.get("value", {})
            if item.get("kind") == "expression_statement" and value.get("kind") == "call":
                if value["name"] == "__gx_draw":
                    output.extend(submit(value)); continue
                if value["name"] == "__gx_load_matrix":
                    args = value["arguments"]
                    if len(args) != 16:
                        raise CompileError("GX load_matrix takes sixteen row-major affine matrix values", value)
                    if [integer(a) for a in args[12:]] != [0, 0, 0, 1]:
                        raise CompileError("GX affine matrix last row must be 0,0,0,1", value)
                    if any(n.get("kind") in {"call", "method"} for n in walk(args)):
                        serial += 1
                        captured = []
                        for index, arg in enumerate(args):
                            name = f"__gx_matrix_{serial}_{index}"
                            declaration = fragment(f"{name}=0", value)[0]
                            declaration["value"] = arg
                            output.append(declaration)
                            captured.append({"kind": "name", "name": name, **{k: value[k] for k in ("line", "column", "filename") if k in value}})
                        args = captured
                    output.extend([call("__tipy_matrix_reset_matrix", [], value), call("__tipy_matrix_apply_matrix", args, value)])
                    continue
            for key, child in list(item.items()):
                if isinstance(child, list) and child and all(isinstance(n, dict) for n in child):
                    item[key] = statements(child)
                elif isinstance(child, dict):
                    item[key] = expression(child)
            output.append(item)
        return output

    def expression(node):
        if node.get("kind") == "call":
            if node["name"] == "__gx_vertex":
                count = len(node["arguments"])
                if count not in (2, 3): raise CompileError("GX vertex takes (x,y) or (x,y,z)", node)
                node["name"] = f"__tipy_native_gx_vertex{count}"
            elif node["name"] == "__gx_load_matrix":
                raise CompileError("GX load_matrix is a statement and does not return a value", node)
            elif node["name"] == "__gx_draw":
                raise CompileError("GX draw is a statement and does not return a value", node)
        for key, child in list(node.items()):
            if isinstance(child, dict): node[key] = expression(child)
            elif isinstance(child, list): node[key] = [expression(n) if isinstance(n, dict) else n for n in child]
        return node

    tree["body"] = statements(tree["body"])
