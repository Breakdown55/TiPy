"""Geometry-group drawing without camera, input, timing, or game policies."""

from copy import deepcopy
from .errors import CompileError
from .scene_data import (
    arguments,
    constant,
    expression,
    fragment,
    load_scene,
    location,
    walk,
)

PUBLIC = {"load_scene", "draw_scene", "set_visible", "scene_collisions"}


def install(tree):
    calls = [node for node in walk(tree["body"]) if node.get("kind") == "call"]
    # Older scripts retain their original behavior through the compatibility
    # layer. Explicit source classes never enter that path.
    defined = {node["name"] for node in tree["body"] if node.get("kind") == "function"}
    if any(
        node.get("name") in {"first_person", "orbit_light"}
        and node["name"] not in defined
        for node in calls
    ):
        return
    declarations = {}
    for statement in tree["body"]:
        value = statement.get("value", {})
        if (
            statement.get("kind") in {"declare", "assign"}
            and value.get("kind") == "call"
            and value.get("name") == "load_scene"
        ):
            spec = {
                "call": value,
                "statement": statement,
                "prefix": f"__tipy_scene_{len(declarations)}",
            }
            spec["setup"] = load_scene(tree, spec)
            visible = {item["visible"] for item in spec["objects"] if item["mutable"]}
            for declaration in spec["setup"]:
                if declaration.get("name") in visible:
                    declaration["static_field"] = True
            declarations[statement["name"]] = spec
    if not declarations:
        return
    helpers = []
    draw_serial = 0

    def scene(node):
        if node.get("kind") != "name" or node["name"] not in declarations:
            raise CompileError("Expected a named scene", node)
        return declarations[node["name"]]

    def draw(call, inside_function=False):
        nonlocal draw_serial
        draw_serial += 1
        args = arguments(
            call,
            ["scene", "shadow_light"],
            {"shadow_light": {"kind": "none", **location(call)}},
        )
        spec = scene(args["scene"])
        light = args["shadow_light"]
        if light.get("kind") == "none":
            light = None
        elif light.get("kind") not in {"list", "tuple"} or len(light["values"]) != 3:
            raise CompileError("shadow_light must be an (x,y,z) tuple", light)
        else:
            light = light["values"]

        prelude = []
        if light:
            for index, value in enumerate(light):
                simple = (
                    value.get("kind") in {"number", "boolean", "name"}
                    or value.get("kind") == "attribute"
                    and value.get("base", {}).get("kind") == "name"
                )
                if not simple:
                    name = f"__tipy_scene_light_{draw_serial}_{index}"
                    statement = fragment(f"int {name}=0", call)[0]
                    statement["value"] = value
                    prelude.append(statement)
                    light[index] = expression(name, call)

        def object_draw(item):
            x, y, z = item["position"]
            compact = (
                light
                and not inside_function
                and item["shadow"]
                and not item["shaded"]
                and y == spec["floor_y"]
            )
            if compact:
                helper_name = spec["prefix"] + "_prop_" + str(len(helpers))
                # Share prop calls when their light expressions and receiver
                # plane match, allowing the normal linker to retain one body.
                signature = (repr(light), y)
                existing = next(
                    (
                        helper
                        for helper in helpers
                        if helper.get("scene_prop_signature") == signature
                    ),
                    None,
                )
                if existing:
                    helper_name = existing["name"]
                else:
                    helper = fragment(
                        f"""
def {helper_name}(int model,int x,int z):
    draw_shadow(model,0,0,0,floor_y={y},x=x,y={y},z=z,max_distance=20)
    draw_mesh(model,x=x,y={y},z=z,cull=False,shaded=False,depth_test=False,outline=True)
""",
                        call,
                    )[0]
                    helper["body"][0]["value"]["arguments"][1:4] = deepcopy(light)
                    helper["scene_prop_signature"] = signature
                    helpers.append(helper)
                result = fragment(f"{helper_name}({item['model']},{x},{z})", call)
            else:
                result = []
                if light and item["shadow"]:
                    shadow = fragment(
                        f"draw_shadow({item['model']},0,0,0,floor_y={spec['floor_y']},x={x},y={y},z={z},max_distance=20)",
                        call,
                    )[0]
                    shadow["value"]["arguments"][1:4] = deepcopy(light)
                    result.append(shadow)
                result.extend(
                    fragment(
                        f"draw_mesh({item['model']},x={x},y={y},z={z},cull=False,shaded={item['shaded']},depth_test=False,outline=True)",
                        call,
                    )
                )
            if item["mutable"]:
                return [
                    {
                        "kind": "if",
                        "condition": expression(item["visible"], call),
                        "body": result,
                        "alternative": [],
                        **location(call),
                    }
                ]
            return result

        def ordered(partition):
            if isinstance(partition, list):
                return [
                    statement for item in partition for statement in object_draw(item)
                ]
            a, b = ordered(partition["negative"]), ordered(partition["positive"])
            middle = (
                object_draw(partition["separator"]) if partition["separator"] else []
            )
            axis = partition["axis"]
            condition = expression(
                f"__tipy_native_mesh_eye_{'xyz'[axis]}() < {partition['plane']}", call
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

        result = object_draw(spec["background"]) if spec["background"] else []
        return prelude + result + ordered(spec["partition"])

    def expressions(node):
        if not isinstance(node, dict):
            return node
        if node.get("kind") == "call" and node.get("name") == "scene_collisions":
            args = arguments(node, ["scene"])
            return deepcopy(scene(args["scene"])["setup"][-1]["value"])
        for key, value in list(node.items()):
            if isinstance(value, dict):
                node[key] = expressions(value)
            elif isinstance(value, list):
                node[key] = [expressions(child) for child in value]
        return node

    def statements(items, inside_function=False):
        result = []
        for statement in items:
            if (
                statement.get("name") in declarations
                and statement is declarations[statement["name"]]["statement"]
            ):
                result.extend(declarations[statement["name"]]["setup"])
                continue
            value = statement.get("value", {})
            if (
                statement.get("kind") == "expression_statement"
                and value.get("kind") == "call"
            ):
                if value["name"] == "draw_scene":
                    result.extend(draw(value, inside_function))
                    continue
                if value["name"] == "set_visible":
                    args = arguments(value, ["scene", "object", "visible"])
                    spec = scene(args["scene"])
                    name = constant(args["object"])
                    item = next(
                        (item for item in spec["objects"] if item["name"] == name), None
                    )
                    if item is None:
                        raise CompileError(
                            "Scene has no object named " + repr(name), args["object"]
                        )
                    result.append(
                        {
                            "kind": "assign",
                            "name": item["visible"],
                            "value": args["visible"],
                            **location(value),
                        }
                    )
                    continue
            for key in ("body", "alternative", "final"):
                if isinstance(statement.get(key), list):
                    statement[key] = statements(
                        statement[key],
                        inside_function or statement.get("kind") == "function",
                    )
            result.append(expressions(statement))
        return result

    tree["body"] = statements(tree["body"]) + helpers
