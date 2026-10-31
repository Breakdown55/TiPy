"""Allocation-free primitive FIFO sharing TiPy's native raster and depth buffer."""
from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub, word


def routines(model=False, fractional=False, depth=False, xyz=True, blank_spans=False):
    out = ["""__gfx_gx_state_active: dw 0
__gfx_gx_state_mode: dw 0
__gfx_gx_state_count: dw 0
__gfx_gx_state_dimensions: dw 0
__gfx_gx_state_shade: dw 0
__gfx_gx_state_cull: dw 0
__gfx_gx_state_depth: dw 0
__gfx_gx_state_vertices: defs 36,0
__gfx_gx_state_edge: db 0,1"""]
    def abort(r, code):
        r.write("@__gfx_gx_state_active", 0); r.write("@__gfx_gx_state_count", 0); r.fail(code)
    r = NativeRoutine("gx_begin", ("mode",))
    r.if_(("ne", word("@__gfx_gx_state_active"), 0), lambda: abort(r, 21))
    r.if_(("uge", "mode", 2), lambda: r.fail(14))
    r.write("@__gfx_gx_state_mode", "mode"); r.write("@__gfx_gx_state_count", 0)
    r.write("@__gfx_gx_state_dimensions", 0); r.write("@__gfx_gx_state_active", 1)
    r.ret(); out.append(r.finish())
    r = NativeRoutine("gx_end")
    r.if_(("eq", word("@__gfx_gx_state_active"), 0), lambda: abort(r, 21))
    r.if_(("ne", word("@__gfx_gx_state_count"), 0), lambda: abort(r, 22))
    r.write("@__gfx_gx_state_active", 0); r.ret(); out.append(r.finish())
    r = NativeRoutine("gx_shade", ("color",))
    r.if_(("uge", "color", 256), lambda: r.fail(14))
    r.expr(add("color", 8))
    r.emit(*[instruction for _ in range(4) for instruction in ("srl h", "rr l")])
    r.emit("ld (__gfx_gx_state_shade),hl"); r.ret(); out.append(r.finish())
    for name in ("cull", "depth_test"):
        r = NativeRoutine("gx_" + name, ("enabled",))
        r.write("@__gfx_gx_state_" + ("depth" if name == "depth_test" else "cull"), "enabled")
        r.ret(); out.append(r.finish())

    for dimensions in (2, 3):
        params = ("x", "y", "z")[:dimensions]
        r = NativeRoutine(f"gx_vertex{dimensions}", params, ("p", "n", "v", "xv", "yv", "zv", "total"))
        r.if_(("eq", word("@__gfx_gx_state_active"), 0), lambda: abort(r, 21))
        r.if_(("eq", word("@__gfx_gx_state_dimensions"), 0), lambda: r.write("@__gfx_gx_state_dimensions", dimensions))
        r.if_(("ne", word("@__gfx_gx_state_dimensions"), dimensions), lambda: abort(r, 21))
        if dimensions == 3:
            r.if_(("eq", word("@__gfx_mesh_ready"), 0), lambda: r.expr(invoke("mesh_init", 75, 2, 120)))
        r.set("n", word("@__gfx_gx_state_count"))
        r.set("total", 0)
        r.set("p", add("@__gfx_gx_state_vertices", invoke("mesh_imul", "n", 12)))
        for axis, label in enumerate(("xv", "yv", "zv")):
            value = params[axis] if axis < dimensions else 0
            if model:
                value = invoke("model_axis", "x", "y", "z" if dimensions == 3 else 0, axis)
            if dimensions == 3:
                value = sub(value, word(add("@__gfx_mesh_eye", axis * 2)))
            r.set(label, value)
            if dimensions == 3:
                r.if_(("slt", label, -120), lambda: r.fail(14))
                r.if_(("slt", 120, label), lambda: r.fail(14))
                r.set("v", label)
                r.if_(("negative", "v"), lambda: r.set("v", sub(0, "v")))
                r.set("total", add("total", "v"))
                r.write(add("@__gfx_mesh_vertex", axis * 2), label)
        if dimensions == 3:
            r.if_(("uge", "total", 121), lambda: r.fail(14))
        if dimensions == 2:
            r.write(add("p", 6), "xv"); r.write(add("p", 8), "yv")
        else:
            for axis in range(3):
                value = invoke("mesh_vertex_dot", add("@__gfx_mesh_basis", axis * 6), "@__gfx_mesh_vertex")
                if fractional: value = sub(value, word(add("@__gfx_mesh_eye_shift", axis * 2)))
                r.write(add("p", axis * 2), value)
        r.set("n", add("n", 1)); r.write("@__gfx_gx_state_count", "n")
        r.set("v", 3)
        r.if_(("eq", word("@__gfx_gx_state_mode"), 1), lambda: r.set("v", 2))
        r.if_(("eq", "n", "v"), lambda: (r.expr(invoke("gx_emit")), r.write("@__gfx_gx_state_count", 0)))
        r.ret(); out.append(r.finish())

    r = NativeRoutine("gx_emit", (), ("poly", "other", "n", "p", "i", "v", "area", "left", "right", "ink"))
    if blank_spans:
        r.emit("xor a", "ld (__gfx_solid_transparent_white),a")
    if xyz:
        r.write("@__gfx_wf_near", word("@__gfx_mesh_near"))
        r.write("@__gfx_wf_far", word("@__gfx_mesh_far"))
        r.write("@__gfx_wf_gain", "@__gfx_mesh_gain")
    def line():
        r.emit("ld a,(__gfx_ink)", "ld l,a", "ld h,0"); r._store("ink")
        r.emit("ld a,(__gfx_gx_state_shade)", "cp 8", "ld a,0", "adc a,0", "ld (__gfx_ink),a")
        def line3():
            r.emit("ld hl,__gfx_gx_state_vertices", "ld de,__gfx_wf_records", "ld bc,24", "ldir")
            r.expr(invoke("wf_draw", 2, 1, "@__gfx_gx_state_edge"))
        def line2(): r.expr(invoke("wf_raster", word("@__gfx_gx_state_vertices+6"), word("@__gfx_gx_state_vertices+8"), word("@__gfx_gx_state_vertices+18"), word("@__gfx_gx_state_vertices+20")))
        if xyz: r.if_(("eq", word("@__gfx_gx_state_dimensions"), 3), line3, line2)
        else: line2()
        r.expr("ink"); r.emit("ld a,l", "ld (__gfx_ink),a"); r.ret()
    r.if_(("eq", word("@__gfx_gx_state_mode"), 1), line)
    if depth:
        r.write("@__gfx_solid_depth_enabled", 0)
        r.if_(("eq", word("@__gfx_gx_state_dimensions"), 3), lambda: r.write("@__gfx_solid_depth_enabled", word("@__gfx_gx_state_depth")))
    r.emit("ld hl,__gfx_gx_state_vertices", "ld de,__gfx_solid_poly_a", "ld bc,36", "ldir")
    r.set("poly", "@__gfx_solid_poly_a"); r.set("other", "@__gfx_solid_poly_b"); r.set("n", 3)
    def clip(axis, bound, keep):
        r.set("n", invoke("solid_clip", "poly", "n", "other", axis, bound, keep))
        r.if_(("ult", "n", 3), lambda: r.ret())
        r.set("v", "poly"); r.set("poly", "other"); r.set("other", "v")
    def project3():
        for bound, keep in (("near", 1), ("far", 0)):
            clip(4, word("@__gfx_mesh_" + bound), keep)
        r.set("p", "poly"); r.set("i", 0)
        def point():
            r.expr(invoke("solid_project", "p"))
            r.set("p", add("p", 12)); r.set("i", add("i", 1))
        r.while_(("ult", "i", "n"), point)
    if xyz: r.if_(("eq", word("@__gfx_gx_state_dimensions"), 3), project3)
    for axis, bound, keep in ((6, 0, 1), (6, 96, 0), (8, 0, 1), (8, 64, 0)):
        clip(axis, bound, keep)
    def cull():
        # After screen clipping, each product and the signed area fit i16.
        r.set("area", 0); r.set("i", 1); r.set("p", add("poly", 12))
        def facing():
            r.set("left", invoke("mesh_imul", sub(word(add("p", 6)), word(add("poly", 6))), sub(word(add("p", 20)), word(add("poly", 8)))))
            r.set("right", invoke("mesh_imul", sub(word(add("p", 8)), word(add("poly", 8))), sub(word(add("p", 18)), word(add("poly", 6)))))
            r.set("area", sub("left", "right")); r.set("i", add("i", 1)); r.set("p", add("p", 12))
        r.while_(("ult", "i", sub("n", 1)), lambda: r.if_(("eq", "area", 0), facing, lambda: r.set("i", "n")))
        r.if_(("slt", "area", 1), lambda: r.ret())
    r.if_(("ne", word("@__gfx_gx_state_cull"), 0), cull)
    def flat(): r.expr(invoke("solid_flat_polygon", "poly", "n", word("@__gfx_gx_state_shade")))
    def filled_depth():
        r.set("i", 1); r.set("p", add("poly", 12))
        def triangle():
            r.expr(invoke("solid_triangle", "poly", "p", add("p", 12), word("@__gfx_gx_state_shade")))
            r.set("p", add("p", 12)); r.set("i", add("i", 1))
        r.while_(("ult", "i", sub("n", 1)), triangle)
    if depth: r.if_(("eq", word("@__gfx_solid_depth_enabled"), 0), flat, filled_depth)
    else: flat()
    r.ret(); out.append(r.finish())
    return out
