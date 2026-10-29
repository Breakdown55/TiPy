"""Compatibility lowering for the original experimental game helper API.

Scene/controller/light handles are static compiler bindings, not heap objects.
Expansion happens before graphics lowering, so the existing constant folding,
mesh specialization, dead-code elimination and demand linker still apply.
"""

from copy import deepcopy
import math

from .errors import CompileError
from .scene_data import (
    _scene_partition,
    arguments,
    constant,
    expression,
    fragment,
    literal,
    load_scene,
    location,
    number,
    walk,
    world,
)

FACTORIES = {"first_person", "orbit_light", "load_scene"}
PUBLIC = FACTORIES | {
    "update_player",
    "player_near",
    "draw_scene",
    "set_visible",
    "clear_rect",
}
PREFIX = "__tipy_game_"


def install(tree):
    defined = {node["name"] for node in tree["body"] if node.get("kind") == "function"}
    calls = [
        node
        for node in walk(tree["body"])
        if node.get("kind") == "call"
        and node.get("name") in PUBLIC
        and node["name"] not in defined
    ]
    if not calls:
        return
    for node in walk(tree["body"]):
        if node.get("name", "").startswith(PREFIX):
            raise CompileError("Game helper names are reserved", node)

    handles = {}
    constructors = set()
    for statement in tree["body"]:
        value = statement.get("value", {})
        if (
            statement.get("kind") == "declare"
            and value.get("kind") == "call"
            and value.get("name") in FACTORIES
            and value["name"] not in defined
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
