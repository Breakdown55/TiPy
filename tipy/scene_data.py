"""Compile-time geometry groups and source-preserving graphics helpers."""

from copy import deepcopy
import math
from .errors import CompileError


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def fragment(source, origin):
    from .lexer import tokenize
    from .parser import parse
    from .stdlib import resolve

    result = parse(tokenize(source.strip() + "\n"))["body"]
    # Library imports are resolved independently of the user's import aliases.
    resolve(result, {None: {"time": "time"}})
    for node in walk(result):
        if "kind" in node:
            for key in ("line", "column", "filename"):
                if key in origin:
                    node[key] = origin[key]
    return result


def expression(source, origin, **values):
    value = fragment("int value = " + source, origin)[0]["value"]

    def substitute(node):
        if isinstance(node, list):
            return [substitute(child) for child in node]
        if not isinstance(node, dict):
            return node
        if node.get("kind") == "name" and node["name"] in values:
            return deepcopy(values[node["name"]])
        return {key: substitute(child) for key, child in node.items()}

    return substitute(value)


def arguments(call, names, defaults=None):
    result = {}
    keyword_seen = False
    position = 0
    for argument in call["arguments"]:
        if argument.get("kind") == "keyword":
            keyword_seen = True
            name, value = argument["name"], argument["value"]
        else:
            if keyword_seen or position >= len(names):
                raise CompileError("Invalid arguments to " + call["name"], call)
            name, value = names[position], argument
            position += 1
        if name not in names or name in result:
            raise CompileError("Unknown or duplicate argument " + name, argument)
        result[name] = value
    for name, value in (defaults or {}).items():
        result.setdefault(
            name, deepcopy(value) if isinstance(value, dict) else literal(value, call)
        )
    missing = set(names) - set(result)
    if missing:
        raise CompileError("Missing argument " + sorted(missing)[0], call)
    return result


def literal(value, origin):
    kind = (
        "boolean"
        if isinstance(value, bool)
        else "string" if isinstance(value, str) else "number"
    )
    return {
        "kind": kind,
        "value": value,
        **{key: origin[key] for key in ("line", "column", "filename") if key in origin},
    }


def constant(node):
    if node.get("kind") in {"number", "decimal", "boolean", "string"}:
        return node["value"]
    if node.get("kind") == "negate":
        value = constant(node["value"])
        if type(value) in {int, float}:
            return -value
    raise CompileError("Game setup options must be literal constants", node)


def number(node, low, high, *, integer=False):
    value = (
        -number(node["value"], -high, -low, integer=integer)
        if node.get("kind") == "negate"
        else constant(node)
    )
    # Decimal tokens retain their source spelling.
    if node.get("kind") == "decimal":
        value = float(value)
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not low <= value <= high
        or integer
        and int(value) != value
    ):
        raise CompileError(
            f"Expected {'an integer' if integer else 'a number'} in {low}..{high}", node
        )
    return int(value) if integer else value


def world(node):
    if node.get("kind") in {"number", "decimal", "negate"}:
        if node.get("kind") != "negate" or node["value"].get("kind") in {
            "number",
            "decimal",
        }:
            return literal(round(number(node, -120, 120) * 16), node)
    return expression("value * 16", node, value=node)


def _scene_partition(items, origin):
    """Build a painter-order BSP only where complete object bounds separate.

    An authored planar wall is preferable to a virtual gap. Intersecting
    objects need the general depth-buffer renderer instead of guessed order.
    """
    if len(items) <= 1:
        return items
    choices = []
    for separator in items:
        for axis in (0, 2, 1):
            low, high = separator["bounds"][axis]
            if low != high:
                continue
            negative, positive = [], []
            for item in items:
                if item is separator:
                    continue
                a, b = item["bounds"][axis]
                if b <= low and a < low:
                    negative.append(item)
                elif a >= low and b > low:
                    positive.append(item)
                else:
                    break
            else:
                if negative and positive:
                    choices.append(
                        (
                            abs(len(negative) - len(positive)),
                            axis,
                            low,
                            negative,
                            separator,
                            positive,
                        )
                    )
    if not choices:
        for axis in (0, 2, 1):
            ordered = sorted(items, key=lambda item: item["bounds"][axis][0])
            for count in range(1, len(items)):
                high = max(item["bounds"][axis][1] for item in ordered[:count])
                low = min(item["bounds"][axis][0] for item in ordered[count:])
                if high <= low:
                    choices.append(
                        (
                            abs(2 * count - len(items)),
                            axis,
                            (low + high) // 2,
                            ordered[:count],
                            None,
                            ordered[count:],
                        )
                    )
    if not choices:
        raise CompileError(
            "Scene objects overlap across every ordering plane; split intersecting objects in the OBJ or use draw_mesh with depth_test=True",
            origin,
        )
    _, axis, plane, negative, separator, positive = min(
        choices, key=lambda choice: choice[0]
    )
    if separator:
        separator["shadow"] = False
    return {
        "axis": axis,
        "plane": plane,
        "negative": _scene_partition(negative, origin),
        "separator": separator,
        "positive": _scene_partition(positive, origin),
    }


def location(node):
    return {key: node[key] for key in ("line", "column", "filename") if key in node}


def load_scene(tree, spec):
    from .scene_assets import objects, select, collision_boxes
    from .obj_loader import load

    call, p = spec["call"], spec["prefix"]
    empty_dict = {"kind": "dict", "entries": [], **location(call)}
    empty_list = {"kind": "list", "values": [], **location(call)}
    options = arguments(
        call,
        ["file", "background", "floor_y", "ambient", "brightness", "unlit", "shadows"],
        {
            "background": "",
            "floor_y": 0,
            "ambient": 14,
            "shadows": True,
            "brightness": empty_dict,
            "unlit": empty_list,
        },
    )
    # The two collection defaults are AST literals, not runtime containers.
    filename = constant(options["file"])
    if (
        not isinstance(filename, str)
        or filename not in tree.get("assets", {})
        or not filename.endswith(".obj")
    ):
        raise CompileError(
            "Open the scene OBJ asset in the project first: " + str(filename), call
        )
    source = tree["assets"][filename]
    background = constant(options["background"])
    spec["floor_y"] = number(options["floor_y"], -120, 120, integer=True)
    spec["ambient"] = number(options["ambient"], 0, 16, integer=True)
    shadows = constant(options["shadows"])
    if type(shadows) is not bool or not isinstance(background, str):
        raise CompileError(
            "Scene background must be an object name and shadows must be True or False",
            call,
        )
    shades = options["brightness"]
    if shades.get("kind") != "dict":
        raise CompileError(
            "Scene brightness must be a literal object-name dictionary", shades
        )
    brightness = {}
    for entry in shades["entries"]:
        name, value = constant(entry["key"]), number(
            entry["value"], 0, 16, integer=True
        )
        if not isinstance(name, str) or name in brightness:
            raise CompileError("Use unique object names in scene brightness", shades)
        brightness[name] = value
    unlit = options["unlit"]
    if unlit.get("kind") not in {"list", "tuple"}:
        raise CompileError("Scene unlit must be a literal list of object names", unlit)
    unlit = {constant(value) for value in unlit["values"]}
    points, groups = objects(source, filename, call)
    names = {
        name
        for name, group in groups.items()
        if group["faces"] and not name.startswith("collider_")
    }
    if (
        not names
        or background
        and background not in names
        or (set(brightness) | unlit) - names
    ):
        raise CompileError("Scene options must name polygon objects in the OBJ", call)
    result, items = [], []
    mutable = set()
    for node in walk(tree["body"]):
        if node.get("kind") == "call" and node.get("name") == "set_visible":
            values = arguments(node, ["scene", "object", "visible"])
            if values["scene"].get("name") == spec["statement"]["name"]:
                mutable.add(constant(values["object"]))
    for name, group in groups.items():
        if name not in names:
            continue
        selected = select(source, filename, name, call)
        vertices, faces = load(selected, filename, 32, call, normalize=False)
        bounds = [
            (
                min(vertex[axis] for vertex in vertices),
                max(vertex[axis] for vertex in vertices),
            )
            for axis in range(3)
        ]
        position = (
            [0, 0, 0]
            if name == background
            or any(low == high for low, high in bounds)
            and name not in unlit
            else [
                (bounds[0][0] + bounds[0][1]) // 2,
                bounds[1][0],
                (bounds[2][0] + bounds[2][1]) // 2,
            ]
        )
        vertices = [
            [vertex[axis] - position[axis] for axis in range(3)] for vertex in vertices
        ]
        _, selected_groups = objects(selected, filename, call)
        edges = sorted(
            {
                tuple(sorted((face[i], face[(i + 1) % len(face)])))
                for group in selected_groups.values()
                for face in group["faces"]
                for i in range(len(face))
            }
        )
        faces = [face + [brightness.get(name, 16)] for face in faces]
        model = f"{p}_model_{len(items)}"
        visible = f"{p}_visible_{len(items)}"
        result.extend(
            fragment(
                f"int {model} = mesh({vertices!r}, edges={edges!r}, faces={faces!r})",
                call,
            )
        )
        if name in mutable:
            result.extend(fragment(f"bool {visible} = True", call))
        items.append(
            {
                "name": name,
                "model": model,
                "visible": visible,
                "mutable": name in mutable,
                "position": position,
                "bounds": bounds,
                "shaded": name not in unlit,
                "shadow": shadows and name != background,
            }
        )
    boxes = (
        collision_boxes(source, filename, call)
        if any(name.startswith("collider_") for name in groups)
        else []
    )
    result.extend(fragment(f"int {p}_walls = collision_map({boxes!r})", call))
    spec["objects"] = items
    spec["background"] = next(
        (item for item in items if item["name"] == background), None
    )
    spec["partition"] = _scene_partition(
        [item for item in items if item is not spec["background"]], call
    )
    return result
