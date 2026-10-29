"""Small graphics conveniences shared by the functional and explicit APIs."""

from .errors import CompileError
from .scene_data import arguments, fragment, number, walk

PUBLIC = {"clear_rect"}


def install(tree):
    serial = 0

    def snapshot(args, serial, origin):
        statements = []
        bindings = {}
        for key, value in args.items():
            if value.get("kind") in {"number", "boolean"}:
                bindings[key] = value
                continue
            name = f"__tipy_draw_{serial}_{key}"
            declaration = fragment(
                f"{'bool' if key=='filled' else 'int'} {name}=0", origin
            )[0]
            declaration["value"] = value
            statements.append(declaration)
            bindings[key] = fragment(f"int value={name}", origin)[0]["value"]
        return statements, bindings

    def statements(items):
        nonlocal serial
        result = []
        for statement in items:
            call = statement.get("value", {})
            if (
                statement.get("kind") == "expression_statement"
                and call.get("kind") == "call"
                and call.get("name")
                in {
                    "__tipy_toolkit_rect",
                    "__tipy_toolkit_circle",
                    "__tipy_toolkit_line",
                }
            ):
                serial += 1
                if call["name"] == "__tipy_toolkit_line":
                    call.update(name="line", raw_graphics=True)
                    result.append(statement)
                    continue
                if call["name"] == "__tipy_toolkit_circle":
                    args = arguments(
                        call, ["x", "y", "radius", "filled"], {"filled": True}
                    )
                    snapshots, args = snapshot(args, serial, call)
                    result.extend(snapshots)
                    call.update(
                        name="__tipy_native_circle",
                        arguments=list(
                            args[key] for key in ("x", "y", "radius", "filled")
                        ),
                    )
                    result.append(statement)
                    continue
                args = arguments(
                    call,
                    ["x", "y", "width", "height", "thickness", "filled"],
                    {"thickness": 1, "filled": True},
                )
                snapshots, bindings = snapshot(args, serial, call)
                result.extend(snapshots)
                replacement = fragment(
                    "if width>0 and height>0:\n    rectangle(x,x+width-1,y,y+height-1,thickness,filled)",
                    call,
                )[0]

                def substitute(value):
                    if isinstance(value, list):
                        return [substitute(child) for child in value]
                    if not isinstance(value, dict):
                        return value
                    if value.get("kind") == "name" and value.get("name") in bindings:
                        from copy import deepcopy

                        return deepcopy(bindings[value["name"]])
                    return {key: substitute(child) for key, child in value.items()}

                result.append(substitute(replacement))
                continue
            if (
                statement.get("kind") == "expression_statement"
                and call.get("kind") == "call"
                and call.get("name") == "clear_rect"
            ):
                args = arguments(call, ["x", "y", "width", "height"])
                try:
                    x, y, w, h = [
                        number(args[key], -32768, 32767, integer=True)
                        for key in ("x", "y", "width", "height")
                    ]
                except CompileError:
                    # Dynamic rectangles use the same clipped pixel operation;
                    # constants take the byte-span/row-fill fast path below.
                    serial += 1
                    prefix = f"__tipy_clip_{serial}"
                    replacement = fragment(
                        f"""
int {prefix}_x=0
int {prefix}_y=0
int {prefix}_w=0
int {prefix}_h=0
for {prefix}_row in range(max(0,{prefix}_y),min(64,{prefix}_y+max(0,{prefix}_h))):
    for {prefix}_column in range(max(0,{prefix}_x),min(96,{prefix}_x+max(0,{prefix}_w))):
        pixel_off({prefix}_column,{prefix}_row)
""",
                        call,
                    )
                    for index, key in enumerate(("x", "y", "width", "height")):
                        replacement[index]["value"] = args[key]
                    result.extend(replacement)
                    continue
                left, right = max(0, x), min(96, x + max(0, w))
                top, bottom = max(0, y), min(64, y + max(0, h))
                if left < right and top < bottom:
                    if left == 0 and right == 96:
                        result.extend(
                            fragment(
                                f"__tipy_native_mesh_clear_rows({top},{bottom-top})",
                                call,
                            )
                        )
                    else:
                        result.extend(
                            fragment(
                                f"for __tipy_row_{len(result)} in range({top},{bottom}):\n    for __tipy_col_{len(result)} in range({left},{right}):\n        pixel_off(__tipy_col_{len(result)},__tipy_row_{len(result)})",
                                call,
                            )
                        )
                continue
            for key in ("body", "alternative", "final"):
                if isinstance(statement.get(key), list):
                    statement[key] = statements(statement[key])
            result.append(statement)
        return result

    tree["body"] = statements(tree["body"])
    if any(
        node.get("kind") == "call"
        and node.get("name") == "__tipy_native_mesh_session_begin"
        for node in walk(tree["body"])
    ):
        tree["game_runtime"] = True
        tree["graphics_session"] = True
