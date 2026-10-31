"""Bounded native model stack and model/view composition for GX meshes."""

from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub, word
from .shape_runtime import words


def routines(normals=True):
    out = ["__gfx_model_depth: dw 0\n__gfx_model_frames: defs 528,0"]
    for popping in (False, True):
        r = NativeRoutine(
            "model_pop" if popping else "model_push", (), ("depth", "address")
        )
        r.set("depth", word("@__gfx_model_depth"))
        if popping:
            r.if_(("eq", "depth", 0), lambda: r.fail(19))
            r.set("depth", sub("depth", 1))
        else:
            r.if_(("uge", "depth", 8), lambda: r.fail(20))
        r.set("address", add("@__gfx_model_frames", invoke("mesh_imul", "depth", 66)))
        r.expr("address")
        if popping:
            r.emit("ld de,__gfx_model", "ld bc,66", "ldir")
        else:
            r.emit("ex de,hl", "ld hl,__gfx_model", "ld bc,66", "ldir")
            r.set("depth", add("depth", 1))
        r.write("@__gfx_model_depth", "depth")
        r.ret()
        out.append(r.finish())
    r = NativeRoutine("model_ink")
    r.emit("ld a,(__gfx_ink)", "ld l,a", "ld h,0", f"jp {r.name}_return")
    out.append(r.finish())

    params = ("x0", "x1", "y0", "y1", "z0", "z1")
    r = NativeRoutine("model_scale", params)
    r.if_(("negative", "x1"), lambda: r.fail(14))
    r.if_(("eq", "x1", 0), lambda: r.if_(("eq", "x0", 0), lambda: r.fail(14)))
    r.expr(invoke("mat_scale", *params))
    r.ret()
    out.append(r.finish())

    out.append(
        "__gfx_model_linear: defs 18,0\n__gfx_model_coeff: defs 18,0\n__gfx_model_normals: defs 18,0"
    )
    r = NativeRoutine(
        "model_mesh_compose",
        ("size",),
        ("i", "j", "p", "v", "x", "y", "z") + words("coefficient", 2),
    )

    def identity():
        r.emit(
            "ld hl,__gfx_mesh_rotation", "ld de,__gfx_model_linear", "ld bc,18", "ldir"
        )
        r.ret()

    r.if_(("eq", word("@__gfx_model_changed"), 0), identity)
    r.write("@__gfx_mesh_identity_active", 0)
    # Convert Q16 coefficients to the bounded mesh kernel's Q8 format.
    r.set("i", 0)
    r.set("p", "@__gfx_model_coeff")

    def row():
        r.set("j", 0)

        def column():
            r.load("coefficient", add("@__gfx_model", add("i", "j")))
            for _ in range(8):
                r.shift("coefficient", signed=True)
            r.if_(
                ("negative", "coefficient0"),
                lambda: r.if_(("ne", "coefficient1", 65535), lambda: r.fail(14)),
                lambda: r.if_(("ne", "coefficient1", 0), lambda: r.fail(14)),
            )
            r.if_(("slt", "coefficient0", -512), lambda: r.fail(14))
            r.if_(("slt", 512, "coefficient0"), lambda: r.fail(14))
            r.write("p", "coefficient0")
            r.set("p", add("p", 2))
            r.set("j", add("j", 4))

        r.while_(("ult", "j", 12), column)
        r.set("i", add("i", 16))

    r.while_(("ult", "i", 48), row)
    r.set("i", 0)

    def world_row():
        r.set("j", 0)

        def world_column():
            r.set(
                "v",
                invoke(
                    "mesh_dot",
                    add("@__gfx_model_coeff", "i"),
                    add("@__gfx_mesh_rotation", "j"),
                    6,
                ),
            )
            # Keep both the intermediate world dot products and the scaled
            # products inside the bounded Q8 mesh kernel's coordinate range.
            for value in ("v", invoke("mesh_mul", "v", "size")):
                r.set("x", value)
                r.if_(("slt", "x", -512), lambda: r.fail(14))
                r.if_(("slt", 512, "x"), lambda: r.fail(14))
            r.write(add("@__gfx_model_linear", add("i", "j")), "v")
            r.set("j", add("j", 2))

        r.while_(("ult", "j", 6), world_column)
        r.set("i", add("i", 6))

    r.while_(("ult", "i", 18), world_row)
    # Positive uniform scale preserves normals after column normalization.
    r.set("j", 0)

    def normal():
        for axis, name in enumerate("xyz"):
            r.set(name, word(add("@__gfx_model_linear", add(axis * 6, "j"))))
        r.expr(invoke("mesh_normalize", "x", "y", "z", "@__gfx_mesh_vertex"))
        for axis in range(3):
            r.write(
                add("@__gfx_model_normals", add(axis * 6, "j")),
                word(add("@__gfx_mesh_vertex", axis * 2)),
            )
        r.set("j", add("j", 2))

    if normals:
        r.while_(("ult", "j", 6), normal)
        r.emit(
            "ld hl,__gfx_model_normals", "ld de,__gfx_mesh_rotation", "ld bc,18", "ldir"
        )
    r.set("i", 0)

    def view_row():
        r.set("j", 0)

        def view_column():
            r.set(
                "v",
                invoke(
                    "mesh_dot",
                    add("@__gfx_mesh_basis", "i"),
                    add("@__gfx_model_linear", "j"),
                    6,
                ),
            )
            r.set("v", invoke("mesh_mul", "v", "size"))
            r.if_(("slt", "v", -512), lambda: r.fail(14))
            r.if_(("slt", 512, "v"), lambda: r.fail(14))
            r.write(add("@__gfx_mesh_view", add("i", "j")), "v")
            if normals:
                r.write(
                    add("@__gfx_mesh_norm_view", add("i", "j")),
                    invoke(
                        "mesh_dot",
                        add("@__gfx_mesh_basis", "i"),
                        add("@__gfx_mesh_rotation", "j"),
                        6,
                    ),
                )
            r.set("j", add("j", 2))

        r.while_(("ult", "j", 6), view_column)
        r.set("i", add("i", 6))

    r.while_(("ult", "i", 18), view_row)
    r.ret()
    out.append(r.finish())
    return out


def line_routines(model_transform=False, fractional_camera=False):
    r = NativeRoutine(
        "mesh_line",
        ("x1", "y1", "z1", "x2", "y2", "z2"),
        ("x", "y", "z", "total", "v", "ink"),
    )
    r.if_(
        ("eq", word("@__gfx_mesh_ready"), 0),
        lambda: r.expr(invoke("mesh_init", 75, 2, 120)),
    )
    for endpoint in (1, 2):
        r.set("total", 0)
        for axis_index, axis in enumerate("xyz"):
            value = (
                invoke(
                    "model_axis",
                    "x" + str(endpoint),
                    "y" + str(endpoint),
                    "z" + str(endpoint),
                    axis_index,
                )
                if model_transform
                else axis + str(endpoint)
            )
            r.set(axis, sub(value, word(add("@__gfx_mesh_eye", axis_index * 2))))
            r.set("v", axis)
            r.if_(("negative", "v"), lambda: r.set("v", sub(0, "v")))
            r.if_(("uge", "v", 121), lambda: r.ret())
            r.set("total", add("total", "v"))
        r.if_(("uge", "total", 121), lambda: r.ret())
        for axis_index, axis in enumerate("xyz"):
            r.write(add("@__gfx_mesh_vertex", axis_index * 2), axis)
        for axis_index in range(3):
            coordinate = invoke(
                "mesh_vertex_dot",
                add("@__gfx_mesh_basis", axis_index * 6),
                "@__gfx_mesh_vertex",
            )
            if fractional_camera:
                coordinate = sub(
                    coordinate, word(add("@__gfx_mesh_eye_shift", axis_index * 2))
                )
            r.write(
                add("@__gfx_wf_records", (endpoint - 1) * 12 + axis_index * 2),
                coordinate,
            )
    for name in ("near", "far"):
        r.write("@__gfx_wf_" + name, word("@__gfx_mesh_" + name))
    r.write("@__gfx_wf_gain", "@__gfx_mesh_gain")
    r.emit("ld a,(__gfx_ink)", "ld l,a", "ld h,0")
    r._store("ink")
    r.emit("ld a,1", "ld (__gfx_ink),a")
    r.expr(invoke("wf_draw", 2, 1, "@__gfx_model_line_edge"))
    r.expr("ink")
    r.emit("ld a,l", "ld (__gfx_ink),a")
    r.ret()
    return [r.finish() + "\n__gfx_model_line_edge: db 0,1"]
