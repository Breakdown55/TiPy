"""Built-in EGX geometry. Geometry is shared and packed at compile time."""
from copy import deepcopy
from .errors import CompileError
from .scene_data import arguments, fragment, literal, number, expression, walk

NAMES = {"__egx_cube", "__egx_plane"}
CUBE_FACES = [(4, 5, 6), (4, 6, 7), (1, 0, 3), (1, 3, 2),
              (0, 4, 7), (0, 7, 3), (5, 1, 2), (5, 2, 6),
              (3, 7, 6), (3, 6, 2), (0, 1, 5), (0, 5, 4)]
OPTIONS = ["x", "y", "z", "yaw", "pitch", "roll", "filled", "shaded", "outline", "depth_test", "brightness"]
DEFAULTS = {"x": 0, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "roll": 0,
            "filled": True, "shaded": True, "outline": True, "depth_test": False, "brightness": 16}


def install(tree):
    models = {}
    serial = 0

    def dimension(value, label):
        try:
            return int(number(value, 1, 118, integer=True))
        except CompileError:
            raise CompileError(f"{label} must be a positive constant dimension; use translate/rotate/scale to animate a shape", value) from None

    def build(call):
        nonlocal serial
        cube = call["name"] == "__egx_cube"
        dimensions = ["size"] if cube else ["width", "depth"]
        native_scale = ["__native_scale"] if call.get("egx_object_draw") else []
        args = arguments(call, dimensions + OPTIONS + native_scale, {**DEFAULTS, **({"size": 12} if cube else {"width": 24, "depth": 24})})
        brightness = int(number(args["brightness"], 0, 16, integer=True))
        dynamic_size = False
        if cube:
            if args["size"].get("kind") in {"number", "negate", "decimal", "boolean"}:
                size = int(number(args["size"], 1, 40, integer=True))
            else:
                # A unit cube scales uniformly without constructing new geometry.
                # Plane dimensions are baked; uniform scale remains available.
                size, dynamic_size = 16, True
            low, high = -(size // 2), size - size // 2
            vertices = [(low, low, low), (high, low, low), (high, high, low), (low, high, low),
                        (low, low, high), (high, low, high), (high, high, high), (low, high, high)]
            faces = CUBE_FACES
        else:
            width, depth = dimension(args["width"], "Plane width"), dimension(args["depth"], "Plane depth")
            left, right = -(width // 2), width - width // 2
            back, front = -(depth // 2), depth - depth // 2
            if max(abs(left), right) + max(abs(back), front) > 60:
                raise CompileError("Plane dimensions exceed the native vertex range; half-width + half-depth must fit within 60", call)
            vertices = [(left, 0, back), (left, 0, front), (right, 0, front), (right, 0, back)]
            faces = [(0, 1, 2), (0, 2, 3)]
        key = (tuple(vertices), brightness)
        if key not in models:
            model_faces = [(*face, brightness) for face in faces]
            model = fragment(f"int model=mesh({vertices!r},faces={model_faces!r})", call)[0]["value"]
            model["shared_graphics"] = True
            models[key] = model
        serial += 1
        snapshots, bound = [], {}
        for name, value in args.items():
            if name in dimensions and not dynamic_size or name == "brightness":
                continue
            if value.get("kind") in {"number", "boolean"}:
                bound[name] = value
            else:
                temp = f"__egx_shape_{serial}_{name}"
                kind = "bool" if name in {"filled", "shaded", "outline", "depth_test"} else "int"
                declaration = fragment(f"{kind} {temp}=0", call)[0]
                declaration["value"] = deepcopy(value)
                snapshots.append(declaration)
                bound[name] = {"kind": "name", "name": temp, **{k: call[k] for k in ("line", "column", "filename") if k in call}}
        draw = fragment("draw_mesh(0)", call)[0]
        draw["value"]["arguments"][0] = deepcopy(models[key])
        draw["value"]["shared_graphics"] = True
        draw["value"]["arguments"].extend({"kind": "keyword", "name": name, "value": bound[name]} for name in OPTIONS if name != "brightness")
        # A plane is two-sided; a cube hides its back faces automatically.
        draw["value"]["arguments"].append({"kind": "keyword", "name": "cull", "value": literal(cube, call)})
        if dynamic_size:
            scale = expression("size*16", call, size=bound["size"])
            if native_scale:
                scale = expression("__tipy_native_mesh_scalar_mul(base,factor)", call, base=scale, factor=bound["__native_scale"])
            draw["value"]["arguments"].append({"kind": "keyword", "name": "size", "value": scale})
            guard = fragment("if size>0 and size<=40:\n    pass", call)[0]
            def substitute(value):
                if isinstance(value, list): return [substitute(v) for v in value]
                if not isinstance(value, dict): return value
                if value.get("kind") == "name" and value["name"] == "size": return deepcopy(bound["size"])
                return {k: substitute(v) for k,v in value.items()}
            guard = substitute(guard)
            guard["body"] = [draw]
            draw = guard
        elif native_scale:
            draw["value"]["arguments"].append({"kind": "keyword", "name": "size", "value": bound["__native_scale"]})
        return snapshots + [draw]

    def statements(items):
        result = []
        for item in items:
            value = item.get("value", {})
            if item.get("kind") == "expression_statement" and value.get("kind") == "call" and value.get("name") in NAMES:
                result.extend(build(value)); continue
            for key, child in list(item.items()):
                if isinstance(child, list) and child and all(isinstance(n, dict) for n in child):
                    item[key] = statements(child)
            if any(n.get("kind") == "call" and n.get("name") in NAMES for n in walk(item) if n is not item):
                raise CompileError("EGX cube and plane draw immediately and do not return a model handle", item)
            result.append(item)
        return result

    body = statements(tree["body"])
    tree["body"] = body
