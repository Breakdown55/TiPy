"""Camera intrinsic signatures; these compile to the calculator's native code."""

CAMERA_PUBLIC = {"init_camera", "camera", "set_fov"}
MATRIX_ENTRIES = {
    "translate": "mat_translate",
    "scale": "mat_scale",
    "rotate": "mat_rotate",
    "rotate_x": "mat_rotate",
    "rotate_y": "mat_rotate",
    "rotate_z": "mat_rotate",
    "push_matrix": "mat_push",
    "pop_matrix": "mat_pop",
    "reset_matrix": "mat_reset",
    "apply_matrix": "mat_apply",
}
INTRINSICS = {
    "mesh_mul": (["a", "b"], ["i16"] * 2, "i16", None),
    "mesh_init": (["fov", "near", "far"], ["i16"] * 3, "void", [75, 2, 120]),
    "mesh_fov": (["fov"], ["i16"], "void", None),
    "mesh_look": (
        ["eye_x", "eye_y", "eye_z", "target_x", "target_y", "target_z"],
        ["i16"] * 6,
        "void",
        [0, 0, 80, 0, 0, 0],
    ),
    "mesh_draw": (
        [
            "model",
            "x",
            "y",
            "z",
            "yaw",
            "pitch",
            "roll",
            "size",
            "filled",
            "cull",
            "shaded",
            "depth_test",
            "outline",
        ],
        ["i16"] * 8 + ["bool"] * 5,
        "void",
        {
            "x": 0,
            "y": 0,
            "z": 0,
            "yaw": 0,
            "pitch": 0,
            "roll": 0,
            "size": 256,
            "filled": True,
            "cull": True,
            "shaded": True,
            "depth_test": True,
            "outline": False,
        },
    ),
    "mesh_collides": (["map", "x", "z", "radius"], ["i16"] * 4, "bool", {"radius": 1}),
    "mesh_shadow": (
        [
            "model",
            "light_x",
            "light_y",
            "light_z",
            "floor_y",
            "x",
            "y",
            "z",
            "max_distance",
        ],
        ["i16"] * 9,
        "void",
        {"floor_y": 0, "x": 0, "y": 0, "z": 0, "max_distance": 240},
    ),
    "solid_light": (["x", "y", "z", "ambient"], ["i16"] * 4, "void", [-1, 2, 3, 3]),
    "solid_no_lights": ([], [], "void", None),
    "solid_ambient_light": (["level"], ["i16"], "void", None),
    "solid_directional_light": (
        ["x", "y", "z", "intensity"],
        ["i16"] * 4,
        "void",
        {"intensity": 16},
    ),
    "solid_point_light": (
        ["x", "y", "z", "radius", "intensity"],
        ["i16"] * 5,
        "void",
        {"radius": 80, "intensity": 16},
    ),
    "cam_init": (["fov", "near", "far"], ["i16"] * 3, "void", [60, 1, 1000]),
    "cam_look": (
        [
            "eye_x",
            "eye_y",
            "eye_z",
            "target_x",
            "target_y",
            "target_z",
            "up_x",
            "up_y",
            "up_z",
        ],
        ["i16"] * 9,
        "void",
        [0, 0, 80, 0, 0, 0, 0, 1, 0],
    ),
    "cam_fov": (["fov"], ["i16"], "void", None),
    "cam_begin": ([], [], "void", None),
    "cam_mode": ([], [], "i16", None),
    "cam_mark": (["mode"], ["i16"], "void", None),
    "cam_point": (["x", "y", "z", "width"], ["i16"] * 4, "void", None),
    "cam_line": (
        ["x1", "y1", "z1", "x2", "y2", "z2", "width"],
        ["i16"] * 7,
        "void",
        None,
    ),
    "cam_batch": (["data", "width"], ["i16"] * 2, "void", None),
    "cam_fast_batch": (["data"], ["i16"], "void", None),
    "cam_fast_look": (
        ["ex", "ey", "ez", "tx", "ty", "tz", "ux", "uy", "uz"],
        ["i16"] * 9,
        "void",
        [0, 0, 0, 0, 0, -1, 0, 1, 0],
    ),
    "cam_vertex": (["x", "y", "z"], ["i16"] * 3, "i16[]", None),
    "cam_fill": (["points"], ["i16[][]"], "void", None),
    "cam_edges": (
        ["points", "closed", "width"],
        ["i16[][]", "bool", "i16"],
        "void",
        None,
    ),
    "model_axis": (["x", "y", "z", "axis"], ["i16"] * 4, "i16", None),
    "model_contour": (["x", "y", "rx", "ry"], ["i16"] * 4, "tuple[i16,i16][]", None),
}


for _fraction_bits in range(1, 5):
    _names, _types, _result, _defaults = INTRINSICS["mesh_look"]
    INTRINSICS[f"mesh_look_q{_fraction_bits}"] = (
        _names,
        _types,
        _result,
        [value * (1 << _fraction_bits) for value in _defaults],
    )
    INTRINSICS[f"mesh_collides_q{_fraction_bits}"] = INTRINSICS["mesh_collides"]


def mesh_unit_vector(values, approximate=False):
    """Integer counterpart of mesh_normalize for literal light directions."""
    from math import isqrt

    signs = [-1 if value < 0 else 1 for value in values]
    values = list(map(abs, values))
    if not any(values) or max(values) > 2047:
        raise ValueError("Invalid bounded direction")
    while max(values) < 1024:
        values = [value * 2 for value in values]
    length = isqrt(sum(value * value for value in values))
    if approximate:
        length = (length >> 3) << 3
    return [
        sign * ((value * 256 + (length >> 1)) // length)
        for sign, value in zip(signs, values)
    ]


def constant_rotation(argument):
    """Match the native Q16 angle reduction and Q8 sine table exactly."""
    if argument["kind"] == "number":
        raw = argument["value"] << 16
    elif argument["kind"] == "decimal":
        raw = argument["raw"] << 8
    elif argument["kind"] == "negate":
        child = argument["value"]
        if child["kind"] == "number":
            raw = -(child["value"] << 16)
        elif child["kind"] == "decimal":
            raw = -(child["raw"] << 8)
        else:
            return None
    else:
        return None
    import math

    phase = (abs(raw) % 411775) >> 8

    def sine(phase):
        phase %= 1608
        negative = phase >= 804
        if negative:
            phase -= 804
        if phase >= 403:
            phase = 804 - phase
        value = round(math.sin(phase * math.pi / 804) * 256) & 255
        if phase and not value:
            value = 256
        return -value if negative else value

    return [(sine(phase) * (-1 if raw < 0 else 1)) << 8, sine(phase + 402) << 8]


def camera_basis(values):
    """Exact integer counterpart of the native normalizer, for constant folding."""
    from math import isqrt

    eye = values[:3]
    target = values[3:6]
    up = values[6:9]

    def normalize(v):
        signs = [-1 if n < 0 else 1 for n in v]
        v = list(map(abs, v))
        if not any(v):
            raise ValueError("Camera eye/target/up vectors are degenerate")
        while max(v) >= 16384:
            v = [n // 2 for n in v]
        while max(v) < 8192:
            v = [n * 2 for n in v]
        length = isqrt(sum(n * n for n in v))
        if not length:
            raise ValueError("Camera eye/target/up vectors are degenerate")
        return [
            sign * n * 16384 // length if sign > 0 else -(n * 16384 // length)
            for sign, n in zip(signs, v)
        ]

    def cross(a, b):
        return [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]

    forward = normalize([b - a for a, b in zip(eye, target)])
    right = normalize(cross(forward, normalize(up)))
    actual_up = normalize(cross(right, forward))
    return [*right, *actual_up, *forward]


def install_helpers(helpers, matrix, dimensions):
    """Change only XYZ entry points; XY entry points retain screen projection."""
    from .parser import parse
    from .lexer import tokenize

    replacement = {
        "__tipy_gfx_begin_shape": "__tipy_gfx_vertices.clear()\n__tipy_gfx_camera_vertices.clear()\n__tipy_native_cam_begin()",
        "__tipy_gfx_vertex2": "__tipy_native_cam_mark(2)\n__tipy_gfx_vertices.append(__tipy_gfx_project(x,y,0))",
        "__tipy_gfx_vertex3": "__tipy_native_cam_mark(3)\n__tipy_gfx_camera_vertices.append(__tipy_native_cam_vertex(x,y,z))",
        "__tipy_gfx_line4": "__tipy_gfx_outline([__tipy_gfx_project(x1,y1,0),__tipy_gfx_project(x2,y2,0)],False)",
        "__tipy_gfx_point2": "__tipy_gfx_outline([__tipy_gfx_project(x,y,0)],False)",
        "__tipy_gfx_line6": "if __tipy_gfx_style[2]!=0:\n    __tipy_gfx_ink(__tipy_gfx_style[3])\n    __tipy_native_cam_line(x1,y1,z1,x2,y2,z2,__tipy_gfx_style[4])",
        "__tipy_gfx_point3": "if __tipy_gfx_style[2]!=0:\n    __tipy_gfx_ink(__tipy_gfx_style[3])\n    __tipy_native_cam_point(x,y,z,__tipy_gfx_style[4])",
        "__tipy_gfx_end_shape": """if __tipy_native_cam_mode()==3:
    if __tipy_gfx_style[0]!=0:
        __tipy_gfx_ink(__tipy_gfx_style[1])
        __tipy_native_cam_fill(__tipy_gfx_camera_vertices)
    if __tipy_gfx_style[2]!=0:
        __tipy_gfx_ink(__tipy_gfx_style[3])
        __tipy_native_cam_edges(__tipy_gfx_camera_vertices,closed,__tipy_gfx_style[4])
else:
    __tipy_gfx_draw_polygon(__tipy_gfx_vertices,closed)
__tipy_gfx_vertices.clear()
__tipy_gfx_camera_vertices.clear()
__tipy_native_cam_begin()""",
        "__tipy_gfx_contour": "__tipy_gfx_polygon(__tipy_native_model_contour(x,y,rx,ry))",
    }
    if matrix:
        replacement["__tipy_gfx_project"] = (
            "return __tipy_native_model_axis(x,y,z,0),__tipy_native_model_axis(x,y,z,1)"
        )
    if 2 not in dimensions:
        replacement["__tipy_gfx_begin_shape"] = (
            "__tipy_gfx_camera_vertices.clear()\n__tipy_native_cam_begin()"
        )
        replacement["__tipy_gfx_end_shape"] = """if __tipy_gfx_style[0]!=0:
    __tipy_gfx_ink(__tipy_gfx_style[1])
    __tipy_native_cam_fill(__tipy_gfx_camera_vertices)
if __tipy_gfx_style[2]!=0:
    __tipy_gfx_ink(__tipy_gfx_style[3])
    __tipy_native_cam_edges(__tipy_gfx_camera_vertices,closed,__tipy_gfx_style[4])
__tipy_gfx_camera_vertices.clear()
__tipy_native_cam_begin()"""
    elif 3 not in dimensions:
        replacement["__tipy_gfx_begin_shape"] = "__tipy_gfx_vertices.clear()"
        replacement["__tipy_gfx_vertex2"] = (
            "__tipy_gfx_vertices.append(__tipy_gfx_project(x,y,0))"
        )
        replacement["__tipy_gfx_end_shape"] = (
            "__tipy_gfx_draw_polygon(__tipy_gfx_vertices,closed)\n__tipy_gfx_vertices.clear()"
        )
    for f in helpers["body"]:
        if f.get("kind") == "function" and f["name"] in replacement:
            source = (
                "def wrapper():\n"
                + "\n".join(
                    "    " + line for line in replacement[f["name"]].splitlines()
                )
                + "\n"
            )
            f["body"] = parse(tokenize(source))["body"][0]["body"]
            if f["name"] == "__tipy_gfx_project":
                f["return_type"] = "tuple[int,int]"
    helpers["body"].insert(
        0, parse(tokenize("int[][] __tipy_gfx_camera_vertices=[]\n"))["body"][0]
    )
    batch = parse(tokenize("""def __tipy_gfx_batch(int data):
    if __tipy_gfx_style[2]!=0:
        __tipy_gfx_ink(__tipy_gfx_style[3])
        __tipy_native_cam_batch(data,__tipy_gfx_style[4])
"""))["body"][0]
    helpers["body"].append(batch)
    helpers["body"].append(parse(tokenize("""def __tipy_gfx_fast_batch(int data):
    if __tipy_gfx_style[2]!=0:
        __tipy_gfx_ink(__tipy_gfx_style[3])
        __tipy_native_cam_fast_batch(data)
"""))["body"][0])
