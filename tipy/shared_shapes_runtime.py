"""Transform convex Easy GX shapes with fixed scratch storage, without lists."""

import math
from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub, word


def routines():
    out = ["__gfx_model_points: defs 128,0"]
    r = NativeRoutine(
        "model_polygon",
        ("count", "filled"),
        (
            "i",
            "j",
            "x1",
            "y1",
            "x2",
            "y2",
            "top",
            "bottom",
            "y",
            "left",
            "right",
            "value",
        ),
    )

    def point(index, x, y):
        r.set(x, word(add("@__gfx_model_points", invoke("mesh_imul", index, 4))))
        r.set(
            y, word(add("@__gfx_model_points", add(invoke("mesh_imul", index, 4), 2)))
        )

    point(0, "x1", "y1")

    def fill():
        r.set("top", "y1")
        r.set("bottom", "y1")
        r.set("i", 1)

        def bounds():
            point("i", "x2", "y2")
            r.if_(("slt", "y2", "top"), lambda: r.set("top", "y2"))
            r.if_(("slt", "bottom", "y2"), lambda: r.set("bottom", "y2"))
            r.set("i", add("i", 1))

        r.while_(("ult", "i", "count"), bounds)
        r.if_(("slt", "top", 0), lambda: r.set("top", 0))
        r.if_(("slt", 63, "bottom"), lambda: r.set("bottom", 63))
        r.set("y", "top")

        def scanline():
            r.set("left", 32767)
            r.set("right", -32768)
            r.set("i", 0)

            def edge():
                r.set("j", add("i", 1))
                r.if_(("eq", "j", "count"), lambda: r.set("j", 0))
                point("i", "x1", "y1")
                point("j", "x2", "y2")
                skip = r.label()
                r.if_(
                    ("slt", "y1", "y2"),
                    lambda: (
                        r.false_jump(("sge", "y", "y1"), skip),
                        r.false_jump(("slt", "y", "y2"), skip),
                    ),
                    lambda: (
                        r.false_jump(("sge", "y", "y2"), skip),
                        r.false_jump(("slt", "y", "y1"), skip),
                    ),
                )
                r.set("value", invoke("intersection", "x1", "y1", "x2", "y2", "y"))
                r.if_(("slt", "value", "left"), lambda: r.set("left", "value"))
                r.if_(("slt", "right", "value"), lambda: r.set("right", "value"))
                r.mark(skip)
                r.set("i", add("i", 1))

            r.while_(("ult", "i", "count"), edge)
            r.if_(
                ("slt", "left", "right"),
                lambda: r.expr(invoke("h_line", "left", "y", sub("right", 1), "y")),
            )
            r.set("y", add("y", 1))

        r.while_(("slt", "y", add("bottom", 1)), scanline)

    r.if_(("ne", "filled", 0), fill)
    r.set("i", 0)

    def border():
        r.set("j", add("i", 1))
        r.if_(("eq", "j", "count"), lambda: r.set("j", 0))
        point("i", "x1", "y1")
        point("j", "x2", "y2")
        r.expr(invoke("line", "x1", "y1", "x2", "y2"))
        r.set("i", add("i", 1))

    r.while_(("ult", "i", "count"), border)
    r.ret()
    out.append(r.finish())

    r = NativeRoutine(
        "model_rect",
        ("x", "y", "width", "height", "thickness", "filled"),
        ("inset", "left", "right", "top", "bottom"),
    )
    for dimension in ("width", "height"):
        r.if_(("slt", dimension, 1), lambda: r.ret())
    r.if_(("ne", "filled", 0), lambda: r.set("thickness", 1))
    r.set("inset", 0)

    def rectangle():
        r.set("left", add("x", "inset"))
        r.set("top", add("y", "inset"))
        r.set("right", sub(add("x", "width"), add("inset", 1)))
        r.set("bottom", sub(add("y", "height"), add("inset", 1)))
        r.if_(("slt", "right", "left"), lambda: r.ret())
        r.if_(("slt", "bottom", "top"), lambda: r.ret())
        for index, (x, y) in enumerate(
            (("left", "top"), ("right", "top"), ("right", "bottom"), ("left", "bottom"))
        ):
            for axis in range(2):
                r.write(
                    add("@__gfx_model_points", index * 4 + axis * 2),
                    invoke("model_axis", x, y, 0, axis),
                )
        r.expr(invoke("model_polygon", 4, "filled"))
        r.set("inset", add("inset", 1))

    r.while_(("slt", "inset", "thickness"), rectangle)
    r.ret()
    out.append(r.finish())

    r = NativeRoutine(
        "model_circle", ("x", "y", "radius", "filled"), ("i", "px", "py", "v")
    )
    r.if_(("negative", "radius"), lambda: r.ret())
    r.if_(("slt", 120, "radius"), lambda: r.fail(14))
    r.set("i", 0)

    def circle_point():
        for offset, center, target in ((0, "x", "px"), (2, "y", "py")):
            r.set(
                "v",
                invoke(
                    "mesh_imul",
                    "radius",
                    word(
                        add(
                            "@__gfx_model_circle_table",
                            add(invoke("mesh_imul", "i", 4), offset),
                        )
                    ),
                ),
            )
            r.expr(add("v", 128))
            r.emit("ld l,h", "ld h,0", "bit 7,l")
            positive = r.label()
            r.emit(f"jr z,{positive}", "ld h,255")
            r.mark(positive)
            r._store("v")
            r.set(target, add(center, "v"))
        for axis in range(2):
            r.write(
                add("@__gfx_model_points", add(invoke("mesh_imul", "i", 4), axis * 2)),
                invoke("model_axis", "px", "py", 0, axis),
            )
        r.set("i", add("i", 1))

    r.while_(("ult", "i", 32), circle_point)
    r.expr(invoke("model_polygon", 32, "filled"))
    r.ret()
    out.append(r.finish())
    table = [
        round(fn(i * math.tau / 32) * 256)
        for i in range(32)
        for fn in (math.cos, math.sin)
    ]
    out.append("__gfx_model_circle_table: dw " + ",".join(map(str, table)))
    # Link the existing exact signed intersection arithmetic without its list ABI.
    from .shape_runtime import _intersection, _multiply

    out += [_intersection(), _multiply(False)]
    return out
