"""Compile small, readable game helpers into ordinary TiPy operations.

Scene/controller/light handles are static compiler bindings, not heap objects.
Expansion happens before graphics lowering, so the existing constant folding,
mesh specialization, dead-code elimination and demand linker still apply.
"""

from copy import deepcopy
import math

from .errors import CompileError

FACTORIES = {"first_person", "orbit_light", "load_scene"}
PUBLIC = FACTORIES | {
    "update_player",
    "player_near",
    "draw_scene",
    "set_visible",
    "clear_rect",
}
PREFIX = "__tipy_game_"


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


def install(tree):
    calls = [
        node
        for node in walk(tree["body"])
        if node.get("kind") == "call" and node.get("name") in PUBLIC
    ]
    if not calls:
        return
    for node in walk(tree["body"]):
        if (
            node.get("name", "").startswith(PREFIX)
            or node.get("kind") == "function"
            and node.get("name", "").split(".")[-1] in PUBLIC
        ):
            raise CompileError("Game helper names are reserved", node)

    handles = {}
    constructors = set()
    for statement in tree["body"]:
        value = statement.get("value", {})
        if (
            statement.get("kind") == "declare"
            and value.get("kind") == "call"
            and value.get("name") in FACTORIES
        ):
            if statement["type"] not in {"int", "i16"} or statement["name"] in handles:
                raise CompileError("Declare each game handle once with int", statement)
            handles[statement["name"]] = {
                "kind": value["name"],
                "prefix": f"{PREFIX}{len(handles)}",
                "call": value,
                "statement": statement,
            }
            constructors.add(id(value))
    for call in calls:
        if call["name"] in FACTORIES and id(call) not in constructors:
            raise CompileError(
                "Create game handles in a top-level int declaration", call
            )

    used = set()
    for call in calls:
        if call["name"] in FACTORIES:
            continue
        for node in walk(call["arguments"]):
            if node.get("kind") == "name" and node["name"] in handles:
                used.add(node["name"])

    def handle(node, kind):
        name = node.get("name") if node.get("kind") == "name" else None
        if name not in handles or handles[name]["kind"] != kind or name not in used:
            raise CompileError(f"Expected a named {kind} handle", node)
        return handles[name]

    def read_options(call, names, defaults):
        return arguments(call, names, defaults)

    heading = f"{PREFIX}heading"
    helpers = []
    if any(handles[name]["kind"] in {"first_person", "orbit_light"} for name in used):
        helpers += fragment(
            f"""
def {heading}(int angle) -> int:
    angle = angle % 128
    int sign = 1
    if angle >= 64:
        angle -= 64
        sign = -1
    int value = angle * (64 - angle) // 8
    value -= value * (128 - value) * 7 // 4096
    return sign * value
""",
            calls[0],
        )

    for name, spec in handles.items():
        if name not in used:
            spec["setup"] = []
            continue
        call, p = spec["call"], spec["prefix"]
        if spec["kind"] == "first_person":
            options = read_options(
                call,
                ["x", "z", "yaw", "speed", "fov", "near", "far"],
                {"x": 0, "z": 0, "yaw": 0, "speed": 8, "fov": 75, "near": 2, "far": 90},
            )
            x = round(number(options["x"], -120, 120) * 16)
            z = round(number(options["z"], -120, 120) * 16)
            yaw = round(number(options["yaw"], -360, 360) * 128 / 360) % 128
            spec["speed"] = number(options["speed"], 1, 16, integer=True)
            fov = number(options["fov"], 10, 150, integer=True)
            near = number(options["near"], 1, 119, integer=True)
            far = number(options["far"], near + 1, 120, integer=True)
            spec["setup"] = fragment(
                f"""
init_camera(fov={fov}, near={near}, far={far})
int {p}_x = {x}
int {p}_z = {z}
int {p}_yaw = {yaw}
int {p}_movement_x = 0
int {p}_movement_z = 0
""",
                call,
            )
        elif spec["kind"] == "orbit_light":
            options = read_options(
                call,
                ["x", "z", "radius", "height", "period", "reach", "intensity"],
                {
                    "x": 0,
                    "z": 0,
                    "radius": 24,
                    "height": 24,
                    "period": 16,
                    "reach": 96,
                    "intensity": 8,
                },
            )
            spec["options"] = {
                key: number(
                    value,
                    (
                        1
                        if key in {"period", "reach"}
                        else 0 if key in {"radius", "intensity"} else -120
                    ),
                    16 if key == "intensity" else 120,
                    integer=True,
                )
                for key, value in options.items()
            }
            spec["setup"] = fragment(
                f"""
int {p}_clock = 0
int {p}_x = 0
int {p}_z = 0
""",
                call,
            )
        else:
            spec["setup"] = load_scene(tree, spec)

    def update_player(call):
        args = arguments(call, ["player", "scene"])
        player = handle(args["player"], "first_person")
        scene = handle(args["scene"], "load_scene")
        p, s = player["prefix"], scene["prefix"]
        helper = f"{p}_{s}_slide"
        if helper not in {node.get("name") for node in helpers}:
            helpers.extend(
                fragment(
                    f"""
def {helper}(int position, int other, int amount, bool horizontal) -> int:
    if amount == 0:
        return position
    int candidate = position + amount
    if horizontal:
        if collides({s}_walls, candidate, other, coordinate_scale=16):
            return position
    else:
        if collides({s}_walls, other, candidate, coordinate_scale=16):
            return position
    return candidate
""",
                    call,
                )
            )

        def direction(angle):
            if player["speed"] == 8:
                return f"{heading}({angle})"
            return f"({heading}({angle}) * {player['speed']} // 8)"

        return fragment(
            f"""
int {p}_turn = int(key_down("right")) - int(key_down("left"))
int {p}_walk = int(key_down("up")) - int(key_down("down"))
int {p}_strafe = 0
if key_down("2nd"):
    {p}_strafe = {p}_turn
    {p}_turn = 0
{p}_yaw = ({p}_yaw + {p}_turn * max(1, {PREFIX}elapsed // 3)) % 128
int {p}_forward_x = {direction(p + '_yaw')}
int {p}_forward_z = -{direction(p + '_yaw + 32')}
{p}_movement_x += ({p}_forward_x * {p}_walk - {p}_forward_z * {p}_strafe) * {PREFIX}elapsed
{p}_movement_z += ({p}_forward_z * {p}_walk + {p}_forward_x * {p}_strafe) * {PREFIX}elapsed
int {p}_step_x = {p}_movement_x // 128
int {p}_step_z = {p}_movement_z // 128
{p}_movement_x %= 128
{p}_movement_z %= 128
{p}_x = {helper}({p}_x, {p}_z, {p}_step_x, True)
{p}_z = {helper}({p}_z, {p}_x, {p}_step_z, False)
""",
            call,
        )

    def draw_scene(call):
        args = arguments(call, ["scene", "camera", "light"])
        scene = handle(args["scene"], "load_scene")
        player = handle(args["camera"], "first_person")
        light = handle(args["light"], "orbit_light")
        p, s, l = player["prefix"], scene["prefix"], light["prefix"]
        options = light["options"]
        radius, period = options["radius"], options["period"]
        divisor = math.gcd(radius, 128)
        orbit_x = f"{heading}({l}_clock // {period}) * {radius // divisor} // {128 // divisor} + {options['x']}"
        orbit_z = f"{heading}({l}_clock // {period} + 32) * {radius // divisor} // {128 // divisor} + {options['z']}"
        statements = fragment(
            f"""
{l}_clock = ({l}_clock + {PREFIX}elapsed) % {period * 128}
{l}_x = {orbit_x}
{l}_z = {orbit_z}
camera({p}_x, 0, {p}_z, {p}_x + {heading}({p}_yaw) * 2, 0, {p}_z - {heading}({p}_yaw + 32) * 2, coordinate_scale=16)
no_lights()
ambient_light({scene['ambient']})
point_light({l}_x, {options['height']}, {l}_z, radius={options['reach']}, intensity={options['intensity']})
""",
            call,
        )

        def draw(item):
            x, y, z = item["position"]
            lines = []
            compact_prop = (
                item["shadow"] and not item["shaded"] and y == scene["floor_y"]
            )
            if compact_prop:
                prop_helper = f"{s}_{l}_prop"
                if prop_helper not in {node.get("name") for node in helpers}:
                    helpers.extend(
                        fragment(
                            f"""
def {prop_helper}(int model, int x, int z):
    draw_shadow(model, {l}_x, {options['height']}, {l}_z, floor_y={y}, x=x, y={y}, z=z, max_distance=20)
    draw_mesh(model, x=x, y={y}, z=z, cull=False, shaded=False, depth_test=False, outline=True)
""",
                            call,
                        )
                    )
                lines.append(f"{prop_helper}({item['model']}, {x}, {z})")
            elif item["shadow"]:
                lines.append(
                    f"draw_shadow({item['model']}, {l}_x, {options['height']}, {l}_z, floor_y={scene['floor_y']}, x={x}, y={y}, z={z}, max_distance=20)"
                )
            if not compact_prop:
                lines.append(
                    f"draw_mesh({item['model']}, x={x}, y={y}, z={z}, cull=False, shaded={item['shaded']}, depth_test=False, outline=True)"
                )
            body = fragment("\n".join(lines), call)
            if not item["mutable"]:
                return body
            return [
                {
                    "kind": "if",
                    "condition": expression(item["visible"], call),
                    "body": body,
                    "alternative": [],
                    **location(call),
                }
            ]

        def ordered(partition):
            if isinstance(partition, list):
                return [node for item in partition for node in draw(item)]
            a = ordered(partition["negative"])
            b = ordered(partition["positive"])
            middle = draw(partition["separator"]) if partition["separator"] else []
            eye = p + ("_x" if partition["axis"] == 0 else "_z")
            condition = (
                expression(f"{eye} < {partition['plane'] * 16}", call)
                if partition["axis"] != 1
                else expression(str(0 < partition["plane"]), call)
            )
            return [
                {
                    "kind": "if",
                    "condition": condition,
                    "body": b + deepcopy(middle) + a,
                    "alternative": deepcopy(a) + middle + deepcopy(b),
                    **location(call),
                }
            ]

        if scene["background"]:
            statements.extend(draw(scene["background"]))
        statements.extend(ordered(scene["partition"]))
        return statements

    def lower_expression(node):
        if not isinstance(node, dict):
            return node
        if node.get("kind") == "call" and node.get("name") == "player_near":
            args = arguments(node, ["player", "x", "z", "radius"], {"radius": 3})
            player = handle(args["player"], "first_person")
            p = player["prefix"]
            return expression(
                f"abs({p}_x - target_x) < distance and abs({p}_z - target_z) < distance",
                node,
                target_x=world(args["x"]),
                target_z=world(args["z"]),
                distance=world(args["radius"]),
            )
        for key, value in list(node.items()):
            if isinstance(value, dict):
                node[key] = lower_expression(value)
            elif isinstance(value, list):
                node[key] = [
                    lower_expression(child) if isinstance(child, dict) else child
                    for child in value
                ]
        return node

    def frame_step(origin):
        return fragment(
            f"""
int {PREFIX}elapsed = __tipy_native_mesh_frame_step()
""",
            origin,
        )

    def has_frame_operations(value):
        if isinstance(value, list):
            return any(has_frame_operations(child) for child in value)
        if not isinstance(value, dict) or value.get("kind") in {
            "while",
            "for",
            "function",
        }:
            return False
        if value.get("kind") == "call" and value.get("name") in {
            "update_player",
            "draw_scene",
        }:
            return True
        return any(has_frame_operations(child) for child in value.values())

    def lower_statements(items, inside_function=False, timed_loop=False):
        result = []
        for statement in items:
            value = statement.get("value", {})
            if id(value) in constructors:
                name = statement["name"]
                result.extend(handles[name]["setup"])
                # There is no runtime handle or allocator to initialize.
                continue
            if statement.get("kind") == "function" and any(
                parameter["name"] in handles for parameter in statement["params"]
            ):
                raise CompileError(
                    "Function parameters cannot shadow game handles", statement
                )
            bindings = set(statement.get("names", []))
            if statement.get("kind") in {"declare", "assign", "for"}:
                bindings.add(statement.get("name"))
            if bindings & handles.keys() or any(
                node.get("kind") == "target_name" and node.get("name") in handles
                for node in walk(statement.get("target"))
            ):
                raise CompileError(
                    "Game handles cannot be reassigned or shadowed", statement
                )
            if (
                statement.get("kind") == "expression_statement"
                and value.get("kind") == "call"
            ):
                name = value["name"]
                if (
                    name in {"update_player", "draw_scene", "set_visible"}
                    and inside_function
                ):
                    raise CompileError(
                        "Use state-changing game helpers in the main game loop", value
                    )
                if name == "update_player":
                    result.extend(
                        ([] if timed_loop else frame_step(value)) + update_player(value)
                    )
                    continue
                if name == "draw_scene":
                    result.extend(
                        ([] if timed_loop else frame_step(value)) + draw_scene(value)
                    )
                    continue
                if name == "set_visible":
                    args = arguments(value, ["scene", "object", "visible"])
                    scene = handle(args["scene"], "load_scene")
                    object_name = constant(args["object"])
                    item = next(
                        (
                            item
                            for item in scene["objects"]
                            if item["name"] == object_name
                        ),
                        None,
                    )
                    if item is None:
                        raise CompileError(
                            "Scene has no object named " + repr(object_name),
                            args["object"],
                        )
                    assignment = fragment(f"{item['visible']} = True", value)[0]
                    assignment["value"] = lower_expression(args["visible"])
                    result.append(assignment)
                    continue
                if name == "clear_rect":
                    args = arguments(value, ["x", "y", "width", "height"])
                    try:
                        x, y, width, height = [
                            number(args[key], -32768, 32767, integer=True)
                            for key in ("x", "y", "width", "height")
                        ]
                    except CompileError:
                        raise CompileError(
                            "clear_rect currently takes constant pixel coordinates and dimensions",
                            value,
                        ) from None
                    left, right = max(0, x), min(96, x + max(0, width))
                    top, bottom = max(0, y), min(64, y + max(0, height))
                    if left < right and top < bottom:
                        if left == 0 and right == 96:
                            result.extend(
                                fragment(
                                    f"__tipy_native_mesh_clear_rows({top}, {bottom - top})",
                                    value,
                                )
                            )
                        else:
                            result.extend(
                                fragment(
                                    f"for {PREFIX}row in range({top}, {bottom}):\n    for {PREFIX}column in range({left}, {right}):\n        pixel_off({PREFIX}column, {PREFIX}row)",
                                    value,
                                )
                            )
                    continue
            for key in ("body", "alternative", "final"):
                if isinstance(statement.get(key), list):
                    frame_loop = (
                        statement.get("kind") in {"while", "for"}
                        and key == "body"
                        and not timed_loop
                        and has_frame_operations(statement[key])
                    )
                    statement[key] = lower_statements(
                        statement[key],
                        inside_function or statement.get("kind") == "function",
                        timed_loop or frame_loop,
                    )
                    if frame_loop:
                        statement[key] = frame_step(statement) + statement[key]
            result.append(lower_expression(statement))
        return result

    body = lower_statements(tree["body"])
    declared = set()
    for node in walk(body):
        if node.get("kind") == "declare" and node.get("name", "").startswith(PREFIX):
            if node["name"] in declared:
                # Separate frame loops can reuse the same compiler scratch
                # slots; this is an assignment after their first declaration.
                node.update(kind="assign", inferred=True)
            declared.add(node["name"])
    # A bare handle is a compile-time binding, never an integer address.
    for node in walk(body):
        if node.get("kind") == "name" and node.get("name") in handles:
            raise CompileError(
                "Use game handles with their game helper functions", node
            )
        if node.get("kind") == "call" and node.get("name") in PUBLIC:
            raise CompileError("Use this game helper as a complete statement", node)
    timed = any(
        node.get("kind") == "call"
        and node.get("name") == "__tipy_native_mesh_frame_step"
        for node in walk(body)
    )
    if timed:
        tree["game_runtime"] = True
        body = (
            fragment("__tipy_native_mesh_game_begin()", calls[0])
            + body
            + fragment(
                "time.stop_frame_timer()\n__tipy_native_mesh_game_end()", calls[-1]
            )
        )
    tree["body"] = helpers + body


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
