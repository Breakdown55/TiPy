"""Point-light shadows on a horizontal plane, rendered by the native mesh path.

The first version projects translated, unrotated models. It reuses the model's
faces and the ordinary camera, clipping, and polygon filler; no host drawing or
depth buffer is involved. A light below a vertex produces no shadow.
"""

from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub, word


def routines(
    capacity=64,
    dynamic_depth=False,
    blank_spans=False,
    checked_coordinates=(),
    above_floor=False,
    compiled_vertices=False,
    fixed_scale=None,
    grounded=False,
):
    routine = NativeRoutine(
        "mesh_shadow",
        ("model", "light_x", "light_y", "light_z", "floor_y", "x", "y", "z"),
        (
            "count",
            "edges",
            "faces",
            "source",
            "record",
            "index",
            "height",
            "distance",
            "scale",
            "world_x",
            "world_y",
            "world_z",
            "old_depth",
        ),
    )
    invalid = routine.label()
    finished = routine.name + "_return"

    def check_coordinate(value, target):
        if value is not None:
            routine.expr(value)
        routine.emit("call __gfx_shadow_coordinate", f"jp nc,{target}")

    for name in ("light_x", "light_y", "light_z", "floor_y", "x", "y", "z"):
        if name not in checked_coordinates:
            check_coordinate(name, invalid)
    routine.set(
        "scale", sub("light_y", "floor_y") if fixed_scale is None else fixed_scale
    )
    if not above_floor:
        routine.emit(
            "ld a,h", "or a", f"jp nz,{finished}", "ld a,l", "or a", f"jp z,{finished}"
        )
    if compiled_vertices:
        if blank_spans:
            routine.emit("xor a", "ld (__gfx_mesh_blank),a")
        routine.emit("ld a,1", "ld (__gfx_mesh_prepare_only),a")
        routine.expr(
            invoke("mesh_draw", "model", "x", "y", "z", 0, 0, 0, 256, 0, 0, 0, 0, 0)
        )
        routine.emit(
            "xor a",
            "ld (__gfx_mesh_prepare_only),a",
            "ld a,(__gfx_mesh_prepared)",
            "or a",
            f"jp z,{finished}",
        )
    else:
        routine.if_(
            ("ne", word("model"), 0x4D54), lambda: routine.emit(f"jp {invalid}")
        )
    routine.set("count", word(add("model", 2)))
    if not compiled_vertices:
        routine.if_(("eq", "count", 0), lambda: routine.emit(f"jp {invalid}"))
        routine.if_(
            ("uge", "count", min(capacity, 64) + 1),
            lambda: routine.emit(f"jp {invalid}"),
        )
    routine.set("edges", word(add("model", 4)))
    routine.set("faces", word(add("model", 8)))
    routine.if_(("eq", "faces", 0), lambda: routine.ret())
    if not compiled_vertices:
        routine.if_(("uge", "faces", 129), lambda: routine.emit(f"jp {invalid}"))
        routine.if_(
            ("eq", word("@__gfx_mesh_ready"), 0),
            lambda: routine.expr(invoke("mesh_init", 75, 2, 120)),
        )
    routine.set("source", add("model", 10))
    routine.set("record", "@__gfx_wf_records")
    routine.set("index", 0)

    def vertex():
        advance = routine.label()
        if compiled_vertices:
            if grounded:
                routine.expr(word(add("source", 2)))
                routine.emit("ld a,h", "or l", f"jp z,{advance}")
            else:
                routine.expr(add(word(add("source", 2)), "y"))
                routine.emit(
                    f"ld e,(ix{routine.slots['floor_y']:+d})",
                    f"ld d,(ix{routine.slots['floor_y']+1:+d})",
                    "or a",
                    "sbc hl,de",
                    f"jp z,{advance}",
                )
        for offset, axis in enumerate("xyz"):
            routine.set("world_" + axis, add(word(add("source", offset * 2)), axis))
            if axis != "y":
                check_coordinate(None, finished)
        routine.set("height", sub("world_y", "floor_y"))
        routine.emit("ld a,h", "or a", f"jp nz,{finished}")
        routine.set("distance", sub("light_y", "world_y"))
        routine.emit(
            "ld a,h", "or a", f"jp nz,{finished}", "ld a,l", "or a", f"jp z,{finished}"
        )

        def project():
            for axis in ("x", "z"):
                routine.set(
                    "world_" + axis,
                    invoke(
                        "wf_lerp", "light_" + axis, "world_" + axis, "scale", "distance"
                    ),
                )

        if compiled_vertices:
            project()
        else:
            routine.if_(("ne", "height", 0), project)
        routine.set("world_y", "floor_y")
        # Keep the camera-relative L1 sum in BC rather than spilling each
        # component through temporary stack slots. Reject before it can wrap.
        routine.emit("ld bc,0")
        for offset, axis in enumerate("xyz"):
            positive = routine.label()
            local = routine.slots["world_" + axis]
            routine.emit(
                f"ld l,(ix{local:+d})",
                f"ld h,(ix{local + 1:+d})",
                f"ld de,(__gfx_mesh_eye+{offset * 2})",
                "or a",
                "sbc hl,de",
                f"ld (__gfx_mesh_vertex+{offset * 2}),hl",
                "bit 7,h",
                f"jr z,{positive}",
                "xor a",
                "sub l",
                "ld l,a",
                "sbc a,a",
                "sub h",
                "ld h,a",
            )
            routine.mark(positive)
            routine.emit(
                "add hl,bc",
                "ld a,h",
                "or a",
                f"jp nz,{finished}",
                "ld a,l",
                "cp 121",
                f"jp nc,{finished}",
                "ld b,h",
                "ld c,l",
            )
        for axis in range(3):
            routine.write(
                add("record", axis * 2),
                invoke(
                    "mesh_vertex_dot",
                    add("@__gfx_mesh_basis", axis * 6),
                    "@__gfx_mesh_vertex",
                ),
            )
        routine.mark(advance)
        routine.set("source", add("source", 6))
        routine.set("record", add("record", 12))
        routine.set("index", add("index", 1))

    routine.while_(("ult", "index", "count"), vertex)
    routine.write("@__gfx_wf_near", word("@__gfx_mesh_near"))
    routine.write("@__gfx_wf_far", word("@__gfx_mesh_far"))
    routine.write("@__gfx_wf_gain", "@__gfx_mesh_gain")
    if dynamic_depth:
        routine.set("old_depth", word("@__gfx_solid_depth_enabled"))
        routine.write("@__gfx_solid_depth_enabled", 0)
    if blank_spans:
        routine.emit("xor a", "ld (__gfx_solid_transparent_white),a")
    routine.expr(invoke("solid_prepare", "count"))
    routine.set("source", add("source", add("edges", "edges")))
    routine.emit("ld a,1", "ld (__gfx_shadow_active),a")
    routine.expr(invoke("solid_draw", "source", "faces", 0, 0))
    routine.emit("xor a", "ld (__gfx_shadow_active),a")
    if dynamic_depth:
        routine.write("@__gfx_solid_depth_enabled", "old_depth")
    routine.ret()
    routine.mark(invalid)
    routine.fail(14)
    coordinate_check = """__gfx_shadow_coordinate:
        ld de,120
        add hl,de
        ld de,241
        or a
        sbc hl,de
        ret
"""
    return [routine.finish(), coordinate_check, "__gfx_shadow_active: db 0"]
