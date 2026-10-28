"""Native clipped triangles, flat lighting, 4x4 dithering and 8-bit inverse Z.

Only solid meshes link this path and its 6144-byte depth buffer. No host draw,
grayscale framebuffer, temporal flicker, or per-frame allocation is involved.
"""

from .camera_math import NativeRoutine, invoke
from .shape_runtime import slot, words
from .list_runtime import add, sub, word, byte


def _copy(r, src, dst, count=12):
    r.expr(src)
    r.emit("push hl")
    r.expr(dst)
    r.emit("ex de,hl", "pop hl", f"ld bc,{count}", "ldir")


def routines(
    extended=False,
    culling=True,
    depth_enabled=True,
    dynamic_depth=False,
    quads=False,
    blank_spans=False,
    fast_spans=True,
):
    out = [
        "__gfx_solid_guard_before: db 165\n__gfx_solid_depth: defs 6144,0\n__gfx_solid_guard_after: db 90",
        "__gfx_solid_flags: defs 64,0",
        "__gfx_solid_light_vector: dw -68,137,205\n__gfx_solid_ambient: dw 3\n__gfx_solid_direction_enabled: dw 1",
        """__gfx_solid_poly_a: defs 144,0
__gfx_solid_poly_b: defs 144,0
__gfx_solid_hits: defs 12,0
__gfx_solid_edges: defs 42,0
__gfx_solid_normal: defs 6,0
__gfx_solid_local_light: defs 6,0
__gfx_solid_pattern: db 0""",
    ]
    bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
    patterns = [
        sum((128 >> x) for x in range(8) if bayer[y][x % 4] >= level)
        for level in range(17)
        for y in range(4)
    ]
    out.append("__gfx_solid_patterns: db " + ",".join(map(str, patterns)))
    out.append("""__gfx_solid_clear:
        ld hl,__gfx_solid_depth
        ld de,__gfx_solid_depth+1
        ld bc,6143
        ld (hl),0
        ldir
        ret""")
    routine = NativeRoutine("solid_light", ("x", "y", "z", "ambient"))
    routine.write("@__gfx_solid_direction_enabled", 1)
    routine.write("@__gfx_light_count", 0)
    routine.write("@__gfx_light_points", 0)
    routine.if_(("slt", "ambient", 0), lambda: routine.fail(14))
    routine.if_(("slt", 16, "ambient"), lambda: routine.fail(14))
    for n in "xyz":
        routine.if_(("slt", n, -2047), lambda: routine.fail(14))
        routine.if_(("slt", 2047, n), lambda: routine.fail(14))
    routine.expr(invoke("mesh_normalize", "x", "y", "z", "@__gfx_solid_light_vector"))
    routine.write("@__gfx_solid_ambient", "ambient")
    routine.ret()
    out.append(routine.finish())

    routine = NativeRoutine(
        "solid_mul32", ("a", "b", "out"), ("negative",) + words("p", 2)
    )
    routine.set("negative", 0)
    for n in ("a", "b"):

        def make_component_positive(n=n):
            routine.set(n, sub(0, n))
            routine.set("negative", sub(1, "negative"))

        routine.if_(
            ("negative", n),
            make_component_positive,
        )
    routine.expr("a")
    routine.emit("ex de,hl")
    routine.expr("b")
    routine.emit("ld b,h", "ld c,l", "call __gfx_wf_umul")
    routine._store("p0")
    routine.emit("ex de,hl")
    routine._store("p1")
    routine.if_(("ne", "negative", 0), lambda: routine.negate("p"))
    routine.save("p", "out")
    routine.ret()
    out.append(routine.finish())
    routine = NativeRoutine(
        "solid_facing", ("normal", "point"), ("i",) + words("sum", 2) + words("p", 2)
    )
    routine.zero("sum")
    routine.set("i", 0)

    def facing():
        routine.expr(
            invoke(
                "solid_mul32",
                word(add("normal", "i")),
                word(add("point", "i")),
                slot("p0"),
            )
        )
        routine.binary("sum", "sum", "p")
        routine.set("i", add("i", 2))

    routine.while_(("ult", "i", 6), facing)
    routine.if_(("negative", "sum1"), lambda: routine.ret(1))
    routine.ret(0)
    out.append(routine.finish())

    routine = NativeRoutine("solid_lerpq", ("a", "b", "n", "d"), ("negative",))
    routine.set("negative", 0)

    def reverse_difference():
        routine.set("negative", 1)
        routine.set("b", sub("a", "b"))

    routine.if_(
        ("ult", "b", "a"),
        reverse_difference,
        lambda: routine.set("b", sub("b", "a")),
    )
    routine.expr("b")
    routine.emit("ex de,hl")
    routine.expr("n")
    routine.emit("ld b,h", "ld c,l", "call __gfx_wf_umul", "push hl")
    routine.expr("d")
    routine.emit("ld b,h", "ld c,l", "pop hl", "call __gfx_wf_udiv")
    routine._store("b")
    routine.if_(("ne", "negative", 0), lambda: routine.ret(sub("a", "b")))
    routine.ret(add("a", "b"))
    out.append(routine.finish())

    routine = NativeRoutine(
        "solid_cut",
        ("outside", "inside", "out", "axis", "bound"),
        ("x", "y", "n", "d", "other"),
    )
    if not depth_enabled:
        wide, positive, forward, denominator = (
            routine.label(),
            routine.label(),
            routine.label(),
            routine.label(),
        )
        routine.expr("axis")
        routine.emit("ld a,l", "cp 4", f"jp z,{wide}")
        routine.expr(word(add("outside", "axis")))
        routine._store("x")
        routine.emit(
            f"ld e,(ix{routine.slots['bound']:+d})",
            f"ld d,(ix{routine.slots['bound']+1:+d})",
            "ld a,h",
            "xor 128",
            "ld h,a",
            "ld a,d",
            "xor 128",
            "ld d,a",
            "or a",
            "sbc hl,de",
            "push af",
            f"jr nc,{positive}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(positive)
        routine._store("n")
        routine.expr(word(add("inside", "axis")))
        routine.emit(
            f"ld e,(ix{routine.slots['x']:+d})",
            f"ld d,(ix{routine.slots['x']+1:+d})",
            "or a",
            "sbc hl,de",
            "pop af",
            f"jr c,{forward}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(forward)
        routine._store("d")
        routine.set("other", sub(14, "axis"))
        routine.write(
            add("out", "other"),
            invoke(
                "wf_lerp",
                word(add("outside", "other")),
                word(add("inside", "other")),
                "n",
                "d",
            ),
        )
        routine.write(add("out", "axis"), "bound")
        routine.ret()
        routine.mark(wide)
    _copy(routine, "outside", "out")
    routine.set("x", word(add("outside", "axis")))
    routine.set("y", word(add("inside", "axis")))

    def measure_forward_intersection():
        routine.set("n", sub("bound", "x"))
        routine.set("d", sub("y", "x"))

    def measure_reverse_intersection():
        routine.set("n", sub("x", "bound"))
        routine.set("d", sub("x", "y"))

    routine.if_(
        ("slt", "x", "y"),
        measure_forward_intersection,
        measure_reverse_intersection,
    )

    def interpolate(offset, q=False):
        routine.write(
            add("out", offset),
            invoke(
                "solid_lerpq" if q else "wf_lerp",
                word(add("outside", offset)),
                word(add("inside", offset)),
                "n",
                "d",
            ),
        )

    def screen_cut():
        routine.set("other", sub(14, "axis"))
        interpolate("other")
        if depth_enabled:
            interpolate(10, True)

    def interpolate_camera_position():
        interpolate(0)
        interpolate(2)

    routine.if_(("eq", "axis", 4), interpolate_camera_position, screen_cut)
    routine.write(add("out", "axis"), "bound")
    routine.ret()
    out.append(routine.finish())

    out.append(_clip())

    out.extend(_edge_dda())
    routine = NativeRoutine("solid_project", ("p",), ("flags", "v"))
    routine.expr(invoke("wf_project", "p"))
    routine.set("flags", word(add("p", 10)))
    routine.expr("flags")
    routine.emit("ld a,l", "and 3", "ld l,a", "ld h,0")
    routine._store("v")

    def depth():
        routine.expr(word("@__gfx_mesh_near"))
        routine.emit(
            "ex de,hl", "ld bc,65280", "call __gfx_wf_umul", "push hl", "push de"
        )
        routine.expr(word(add("p", 4)))
        routine.emit("ld b,h", "ld c,l", "pop de", "pop hl", "call __gfx_wf_udiv")
        routine._store("v")
        routine.write(add("p", 10), "v")

    if depth_enabled:
        routine.if_(("eq", "v", 0), depth)
    routine.ret("flags")
    out.append(routine.finish())
    routine = NativeRoutine("solid_prepare", ("count",), ("i", "p"))
    routine.set("i", 0)
    routine.set("p", "@__gfx_wf_records")

    def prepare():
        routine.write(add("@__gfx_solid_flags", "i"), invoke("solid_project", "p"), 1)
        routine.set("p", add("p", 12))
        routine.set("i", add("i", 1))

    routine.while_(("ult", "i", "count"), prepare)
    routine.ret()
    out.append(routine.finish())
    out.append(_span() if depth_enabled else _flat_span())

    routine = NativeRoutine(
        "solid_triangle",
        ("a", "b", "c", "shade"),
        ("y", "end", "v", "n", "out", "left", "right"),
    )
    for i, (a, b) in enumerate((("a", "b"), ("b", "c"), ("c", "a"))):
        routine.expr(
            invoke("solid_edge_setup", a, b, add("@__gfx_solid_edges", i * 14))
        )
    routine.set("y", word(add("a", 8)))
    routine.set("end", "y")
    for p in ("b", "c"):
        routine.set("v", word(add(p, 8)))
        routine.if_(("slt", "v", "y"), lambda: routine.set("y", "v"))
        routine.if_(("slt", "end", "v"), lambda: routine.set("end", "v"))

    def row():
        routine.set("n", 0)
        routine.set("out", "@__gfx_solid_hits")
        for i in range(3):

            def append_intersection():
                routine.set("n", add("n", 1))
                routine.set("out", add("out", 4))

            routine.if_(
                (
                    "ne",
                    invoke(
                        "solid_edge_read", add("@__gfx_solid_edges", i * 14), "y", "out"
                    ),
                    0,
                ),
                append_intersection,
            )

        def span():
            routine.set("left", "@__gfx_solid_hits")
            routine.set("right", "@__gfx_solid_hits+4")

            def swap_span_ends():
                routine.set("v", "left")
                routine.set("left", "right")
                routine.set("right", "v")

            routine.if_(
                ("ult", word("right"), word("left")),
                swap_span_ends,
            )
            routine.expr(
                invoke(
                    "solid_span",
                    word("left"),
                    word(add("left", 2)),
                    word("right"),
                    word(add("right", 2)),
                    "y",
                    "shade",
                )
            )

        routine.if_(("eq", "n", 2), span)
        routine.set("y", add("y", 1))

    routine.while_(("ult", "y", "end"), row)
    routine.ret()
    out.append(routine.finish())
    if not depth_enabled or dynamic_depth:
        out.extend(
            [
                "__gfx_solid_chain_x: defs 64,0",
                _fast_span() if fast_spans else _basic_span(),
                _flat_polygon(blank_spans=blank_spans),
            ]
        )
    out.append(
        _draw(extended, culling, depth_enabled, dynamic_depth, quads, blank_spans)
    )
    if not depth_enabled:
        out[0] = ""
        out = [
            (
                "__gfx_solid_clear:\n        ret"
                if s.startswith("__gfx_solid_clear:")
                else s
            )
            for s in out
        ]
    if dynamic_depth:
        out = [
            s.replace(
                "__gfx_solid_span:\n",
                "__gfx_solid_span:\n        ld a,(__gfx_solid_depth_enabled)\n        or a\n        jp z,__gfx_solid_flat_span\n",
            )
            for s in out
        ]
        out.extend(
            [
                "__gfx_solid_depth_enabled: dw 1",
                _flat_span().replace("__gfx_solid_span", "__gfx_solid_flat_span"),
            ]
        )
    return out


def _clip():
    """Sutherland-Hodgman with register-held cursor and byte predicates."""
    routine = NativeRoutine(
        "solid_clip",
        ("src", "count", "dst", "axis", "bound", "keep"),
        ("prev", "ip", "ic", "out", "n", "outside", "inside"),
    )
    routine.if_(("eq", "count", 0), lambda: routine.ret(0))
    routine.emit("push iy")
    routine.expr("src")
    routine.emit("push hl", "pop iy")
    routine.expr("count")
    routine.emit(
        "dec hl",
        "add hl,hl",
        "add hl,hl",
        "ld d,h",
        "ld e,l",
        "add hl,hl",
        "add hl,de",
        "push hl",
    )
    routine.expr("src")
    routine.emit("pop de", "add hl,de")
    routine._store("prev")
    routine.set("out", "dst")
    routine.set("n", 0)

    def value(point):
        if point == "current":
            routine.emit("push iy", "pop hl")
        else:
            routine.expr(point)
        routine.emit(
            f"ld e,(ix{routine.slots['axis']:+d})",
            f"ld d,(ix{routine.slots['axis']+1:+d})",
            "add hl,de",
            "ld e,(hl)",
            "inc hl",
            "ld h,(hl)",
            "ld l,e",
            f"ld e,(ix{routine.slots['bound']:+d})",
            f"ld d,(ix{routine.slots['bound']+1:+d})",
        )

    def inside(point):
        equal, done = routine.label(), routine.label()
        value(point)
        routine.emit(
            "ld a,h",
            "xor 128",
            "ld h,a",
            "ld a,d",
            "xor 128",
            "ld d,a",
            "or a",
            "sbc hl,de",
            f"jr z,{equal}",
            "ld a,0",
            "adc a,0",
            "ld c,a",
            f"ld a,(ix{routine.slots['keep']:+d})",
            "xor c",
            f"jr {done}",
        )
        routine.mark(equal)
        routine.emit("ld a,1")
        routine.mark(done)

    inside("prev")
    routine.emit(
        f"ld (ix{routine.slots['ip']:+d}),a", f"ld b,(ix{routine.slots['count']:+d})"
    )
    loop, no_cross, selected, no_append = (routine.label() for _ in range(4))
    routine.mark(loop)
    routine.emit("push bc")
    inside("current")
    routine.emit(
        f"ld (ix{routine.slots['ic']:+d}),a",
        "ld c,a",
        f"ld a,(ix{routine.slots['ip']:+d})",
        "xor c",
        f"jp z,{no_cross}",
    )
    routine.set("outside", "prev")
    routine.emit("push iy", "pop hl")
    routine._store("inside")
    routine.emit(f"ld a,(ix{routine.slots['ip']:+d})", "or a", f"jr z,{selected}")
    routine.set("inside", "prev")
    routine.emit("push iy", "pop hl")
    routine._store("outside")
    routine.mark(selected)
    value("inside")
    routine.emit("or a", "sbc hl,de", f"jp z,{no_cross}")
    for argument in ("outside", "inside", "out", "axis", "bound"):
        routine.expr(argument)
        routine.emit("push hl")
    routine.emit("call __gfx_solid_cut", "ld hl,10", "add hl,sp", "ld sp,hl")
    routine.expr("out")
    routine.emit("ld de,12", "add hl,de")
    routine._store("out")
    routine.emit(f"inc (ix{routine.slots['n']:+d})")
    routine.mark(no_cross)
    routine.emit(
        f"ld a,(ix{routine.slots['ic']:+d})",
        "or a",
        f"jr z,{no_append}",
        "push iy",
        "pop hl",
        f"ld e,(ix{routine.slots['out']:+d})",
        f"ld d,(ix{routine.slots['out']+1:+d})",
        "ld bc,12",
        "ldir",
        f"ld (ix{routine.slots['out']:+d}),e",
        f"ld (ix{routine.slots['out']+1:+d}),d",
        f"inc (ix{routine.slots['n']:+d})",
    )
    routine.mark(no_append)
    routine.emit("push iy", "pop hl")
    routine._store("prev")
    routine.emit(
        f"ld a,(ix{routine.slots['ic']:+d})",
        f"ld (ix{routine.slots['ip']:+d}),a",
        "ld de,12",
        "add iy,de",
        "pop bc",
        "dec b",
        f"jp nz,{loop}",
        "pop iy",
    )
    routine.ret("n")
    return routine.finish()


def _flat_span():
    """Stack ABI wrapper plus a register-only opaque scanline kernel."""
    routine = NativeRoutine("solid_span", ("lx", "lq", "rx", "rq", "y", "shade"))
    for value, register in (("lx", "b"), ("rx", "c")):
        routine.expr(add(value, 127))
        routine.emit(f"ld {register},h")
    routine.expr("y")
    routine.emit("ld d,l")
    routine.expr("shade")
    routine.emit("ld e,l", "call __gfx_solid_span_row")
    routine.ret()
    return routine.finish() + """
; B=start, C=end (exclusive), D=y, E=shade. IX/IY preserved.
__gfx_solid_span_row:
        ld a,c
        sub b
        ret z
        ret c
        push bc
        push de
        ld a,d
        and 3
        ld l,a
        ld h,0
        ld a,e
        add a,a
        add a,a
        ld e,a
        ld d,0
        add hl,de
        ld de,__gfx_solid_patterns
        add hl,de
        ld a,(hl)
        ld (__gfx_solid_pattern),a
        pop de
        pop bc
        ld l,d
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld a,b
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
        ld de,0x9340
        add hl,de
        push hl
        ld a,c
        dec a
        and 7
        ld l,a
        ld h,0
        ld de,__gfx_solid_span_last_masks
        add hl,de
        ld a,(hl)
        push af
        ld a,b
        and 7
        ld l,a
        ld h,0
        ld de,__gfx_solid_span_first_masks
        add hl,de
        ld d,(hl)
        pop af
        ld e,a
        ld a,c
        dec a
        srl a
        srl a
        srl a
        ld c,a
        ld a,b
        srl a
        srl a
        srl a
        ld b,a
        ld a,c
        sub b
        ld b,a
        ld a,(__gfx_solid_pattern)
        ld c,a
        pop hl
        ld a,b
        or a
        jr nz,__gfx_solid_span_multiple
        ld a,d
        and e
        ld d,a
        jr __gfx_solid_span_blend
__gfx_solid_span_multiple:
        ld a,c
        xor (hl)
        and d
        xor (hl)
        ld (hl),a
        inc hl
        dec b
        jr z,__gfx_solid_span_last
__gfx_solid_span_middle:
        ld (hl),c
        inc hl
        djnz __gfx_solid_span_middle
__gfx_solid_span_last:
        ld d,e
__gfx_solid_span_blend:
        ld a,c
        xor (hl)
        and d
        xor (hl)
        ld (hl),a
        ret
__gfx_solid_span_first_masks: db 255,127,63,31,15,7,3,1
__gfx_solid_span_last_masks: db 128,192,224,240,248,252,254,255

"""


def _basic_span():
    source = _flat_span()
    start = source.index("        push hl\n        ld a,c\n        dec a")
    end = source.index("        ld a,(__gfx_solid_pattern)", start)
    tail = source[end:].replace(
        "        ld a,(__gfx_solid_pattern)", "        ex af,af'"
    )
    prefix = """__gfx_solid_fast_span:
        ld a,c
        sub b
        ret z
        ret c
        ld a,d
        ex af,af'
        ld a,b
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
"""
    return (prefix + source[start:end] + tail).replace(
        "__gfx_solid_span_", "__gfx_solid_fast_span_"
    )


def _fast_span():
    # Two aligned pages map x directly to (mask, byte index), removing all
    # per-row shifts and variable pointer arithmetic from span setup.
    source = _basic_span()
    tail = source[
        source.index("        ld a,b\n        or a") : source.index(
            "__gfx_solid_fast_span_first_masks:"
        )
    ]
    prefix = """__gfx_solid_fast_span:
        ld a,c
        sub b
        ret z
        ret c
        ld a,d
        ex af,af'
        push hl
        ld a,b
        add a,a
        ld l,a
        ld h,__gfx_solid_fast_span_tables >> 8
        ld d,(hl)
        inc hl
        ld b,(hl)
        ld a,c
        add a,a
        ld l,a
        inc h
        ld e,(hl)
        inc hl
        ld c,(hl)
        ld a,c
        sub b
        ld c,a
        pop hl
        ld a,l
        add a,b
        ld l,a
        jr nc,__gfx_solid_fast_span_address
        inc h
__gfx_solid_fast_span_address:
        ld b,c
        ex af,af'
        ld c,a
"""
    first = []
    last = []
    for x in range(128):
        first.extend((255 >> (x & 7), x >> 3))
        last.extend(((255 << (7 - ((x - 1) & 7))) & 255, ((x - 1) & 255) >> 3))
    return (
        prefix
        + tail
        + "__gfx_solid_fast_span_tables: db "
        + ",".join(map(str, first + last))
    )


def _edge_dda():
    routine = NativeRoutine(
        "solid_edge_setup",
        ("a", "b", "out"),
        ("ya", "yb", "d", "v", "x0", "x1", "q0", "q1", "negative"),
    )
    routine.set("ya", word(add("a", 8)))
    routine.set("yb", word(add("b", 8)))

    def swap_edge_endpoints():
        routine.set("v", "a")
        routine.set("a", "b")
        routine.set("b", "v")
        routine.set("v", "ya")
        routine.set("ya", "yb")
        routine.set("yb", "v")

    routine.if_(
        ("slt", "yb", "ya"),
        swap_edge_endpoints,
    )
    routine.write("out", "ya")
    routine.write(add("out", 2), "yb")
    routine.if_(("eq", "ya", "yb"), lambda: routine.ret())
    routine.set("d", sub("yb", "ya"))
    for n, p in (("x0", "a"), ("x1", "b")):
        routine.expr(word(add(p, 6)))
        routine.emit("ld h,l", "ld l,0")
        routine._store(n)
    routine.write(add("out", 4), invoke("wf_lerp", "x0", "x1", 1, add("d", "d")))
    routine.set("q0", word(add("a", 10)))
    routine.set("q1", word(add("b", 10)))
    routine.write(add("out", 6), invoke("solid_lerpq", "q0", "q1", 1, add("d", "d")))
    for x, y, offset, unsigned in (("x0", "x1", 8, False), ("q0", "q1", 10, True)):
        routine.set("negative", 0)

        def reverse_edge_slope(x=x, y=y):
            routine.set("negative", 1)
            routine.set("v", sub(x, y))

        routine.if_(
            ("ult" if unsigned else "slt", y, x),
            reverse_edge_slope,
            lambda x=x, y=y: routine.set("v", sub(y, x)),
        )
        routine.expr("d")
        routine.emit("ld b,h", "ld c,l")
        routine.expr("v")
        routine.emit("ld de,0", "call __gfx_wf_udiv")
        routine._store("v")
        if unsigned:
            routine.write(add("out", 12), "negative")
        else:
            routine.if_(("ne", "negative", 0), lambda: routine.set("v", sub(0, "v")))
        routine.write(add("out", offset), "v")
    routine.ret()
    setup = routine.finish()
    routine = NativeRoutine("solid_edge_read", ("edge", "y", "out"), ("v",))
    routine.if_(("slt", "y", word("edge")), lambda: routine.ret(0))
    routine.if_(("sge", "y", word(add("edge", 2))), lambda: routine.ret(0))
    _copy(routine, add("edge", 4), "out", 4)
    routine.write(add("edge", 4), add(word(add("edge", 4)), word(add("edge", 8))))
    routine.set("v", word(add("edge", 6)))
    routine.if_(
        ("ne", word(add("edge", 12)), 0),
        lambda: routine.set("v", sub("v", word(add("edge", 10)))),
        lambda: routine.set("v", add("v", word(add("edge", 10)))),
    )
    routine.write(add("edge", 6), "v")
    routine.ret(1)
    return [setup, routine.finish()]


def _span():
    routine = NativeRoutine(
        "solid_span",
        ("lx", "lq", "rx", "rq", "y", "shade"),
        ("start", "end", "q", "step", "negative", "n", "d", "v"),
    )
    for name, x in (("start", "lx"), ("end", "rx")):
        routine.expr(add(x, 127))
        routine.emit("ld l,h", "ld h,0")
        routine._store(name)
    routine.if_(("uge", "start", "end"), lambda: routine.ret())
    routine.set("d", sub("rx", "lx"))
    routine.expr("start")
    routine.emit("ld h,l", "ld l,128")
    routine._store("v")
    routine.set("n", sub("v", "lx"))
    routine.set("q", invoke("solid_lerpq", "lq", "rq", "n", "d"))
    routine.set("negative", 0)

    def reverse_depth_slope():
        routine.set("negative", 1)
        routine.set("v", sub("lq", "rq"))

    routine.if_(
        ("ult", "rq", "lq"),
        reverse_depth_slope,
        lambda: routine.set("v", sub("rq", "lq")),
    )
    routine.expr("v")
    routine.emit("ld d,h", "ld e,l", "ld bc,256", "call __gfx_wf_umul", "push hl")
    routine.expr("d")
    routine.emit("ld b,h", "ld c,l", "pop hl", "call __gfx_wf_udiv")
    routine._store("step")
    routine.expr("y")
    routine.emit("ld a,l", "and 3", "ld c,a", "ld b,0")
    routine.expr("shade")
    routine.emit(
        "add hl,hl",
        "add hl,hl",
        "add hl,bc",
        "ld de,__gfx_solid_patterns",
        "add hl,de",
        "ld a,(hl)",
        "ld (__gfx_solid_pattern),a",
    )
    routine.code.extend(_span_setup(routine))
    for direction in (0, 1):
        name = f"__gfx_solid_span_{direction}"
        routine.mark(name)
        routine.emit(
            "ld a,(ix+0)",
            "cp h",
            f"jr c,{name}_new",
            f"jr nz,{name}_next",
            "ld a,(__gfx_solid_pattern)",
            "and c",
            f"jr z,{name}_next",
            f"jr {name}_black",
        )
        routine.mark(name + "_new")
        routine.emit(
            "ld (ix+0),h",
            "ld a,(__gfx_solid_pattern)",
            "and c",
            f"jr nz,{name}_black",
            "ld a,c",
            "cpl",
            "and (iy+0)",
            f"jr {name}_store",
        )
        routine.mark(name + "_black")
        routine.emit("ld a,(iy+0)", "or c")
        routine.mark(name + "_store")
        routine.emit("ld (iy+0),a")
        routine.mark(name + "_next")
        routine.emit("inc ix", "rrc c", f"jr nc,{name}_same", "inc iy")
        routine.mark(name + "_same")
        routine.emit(
            *(["or a", "sbc hl,de"] if direction else ["add hl,de"]),
            f"djnz {name}",
            "pop ix",
            "pop iy",
            f"jp {routine.name}_return",
        )
    return routine.finish()


def _span_setup(r):
    # Generate setup in a temporary emitter with the same frame layout.
    t = NativeRoutine("solid_span_setup")
    t.slots = r.slots
    t.code = []
    t.emit("push iy")
    t.expr("y")
    t.emit(
        "add hl,hl",
        "add hl,hl",
        "push hl",
        "add hl,hl",
        "pop de",
        "add hl,de",
        "push hl",
    )
    t.expr("start")
    t.emit(
        "srl l",
        "srl l",
        "srl l",
        "pop de",
        "add hl,de",
        "ld de,0x9340",
        "add hl,de",
        "push hl",
        "pop iy",
    )
    t.expr("y")
    t.emit(
        "add hl,hl",
        "add hl,hl",
        "push hl",
        "add hl,hl",
        "pop de",
        "add hl,de",
        "add hl,hl",
        "add hl,hl",
        "add hl,hl",
        "push hl",
    )
    t.expr("start")
    t.emit("pop de", "add hl,de", "ld de,__gfx_solid_depth", "add hl,de", "push hl")
    t.expr("start")
    t.emit(
        "ld a,l",
        "and 7",
        "ld b,a",
        "ld a,128",
        "inc b",
        "jr __gfx_solid_span_mask_ready",
    )
    t.mark("__gfx_solid_span_mask_shift")
    t.emit("rrca")
    t.mark("__gfx_solid_span_mask_ready")
    t.emit("djnz __gfx_solid_span_mask_shift", "ld c,a")
    t.expr(sub("end", "start"))
    t.emit("ld b,l")
    t.expr("negative")
    t.emit("ld a,l", "or a", "pop hl", "push ix", "push hl")
    t.expr("step")
    t.emit("ex de,hl")
    t.expr("q")
    t.emit("pop ix", "jp nz,__gfx_solid_span_1", "jp __gfx_solid_span_0")
    return t.code


def _flat_polygon(row_name="solid_span_row", blank_spans=False):
    """Walk the two monotone chains of a convex clipped polygon.

    The first chain stores one intersection per row; the second emits spans
    directly. No per-row min/max initialization or second bounds pass.
    Scanline coherence: graphics.stanford.edu/courses/cs248-08/scan/scan1.html
    """
    routine = NativeRoutine(
        "solid_flat_polygon",
        ("poly", "count", "shade"),
        (
            "first",
            "last",
            "top_point",
            "bottom_point",
            "top",
            "bottom",
            "p",
            "next",
            "stride",
            "stride_mask",
        ),
    )
    routine.set("stride", 1)
    if blank_spans:
        ordinary, twice = routine.label(), routine.label()
        routine.emit("ld a,(__gfx_solid_transparent_white)", "or a", f"jr z,{ordinary}")
        routine.expr("shade")
        routine.emit("ld a,l", "cp 12", f"jr c,{ordinary}", "cp 15", f"jr c,{twice}")
        routine.set("stride", 4)
        routine.emit(f"jr {ordinary}")
        routine.mark(twice)
        routine.set("stride", 2)
        routine.mark(ordinary)
    routine.expr(sub("stride", 1))
    routine._store("stride_mask")
    routine.set("top_point", 0)
    routine.set("bottom_point", 0)
    extent, next_top, next_bottom, advance = (routine.label() for _ in range(4))
    routine.expr("count")
    routine.emit("ld b,l", "ld c,0")
    routine.expr("poly")
    routine.emit("ld de,8", "add hl,de", "ld d,64", "ld e,0")
    routine.mark(extent)
    routine.emit(
        "ld a,(hl)",
        "cp d",
        f"jr nc,{next_top}",
        "ld d,a",
        f"ld (ix{routine.slots['top_point']:+d}),c",
    )
    routine.mark(next_top)
    routine.emit(
        "cp e",
        f"jr c,{next_bottom}",
        f"jr z,{next_bottom}",
        "ld e,a",
        f"ld (ix{routine.slots['bottom_point']:+d}),c",
    )
    routine.mark(next_bottom)
    routine.emit("ld a,l", "add a,12", "ld l,a", f"jr nc,{advance}", "inc h")
    routine.mark(advance)
    routine.emit("inc c", f"djnz {extent}")
    routine.emit("ld l,d", "ld h,0")
    routine._store("top")
    routine.emit("ld l,e")
    routine._store("bottom")
    routine.if_(("uge", "top", "bottom"), lambda: routine.ret())
    for point in ("top_point", "bottom_point"):
        routine.expr(point)
        routine.emit(
            "add hl,hl",
            "add hl,hl",
            "ld d,h",
            "ld e,l",
            "add hl,hl",
            "add hl,de",
            "push hl",
        )
        routine.expr("poly")
        routine.emit("pop de", "add hl,de")
        routine._store(point)
    routine.set("first", "poly")
    routine.expr("count")
    routine.emit(
        "dec hl",
        "add hl,hl",
        "add hl,hl",
        "ld d,h",
        "ld e,l",
        "add hl,hl",
        "add hl,de",
        "push hl",
    )
    routine.expr("poly")
    routine.emit("pop de", "add hl,de")
    routine._store("last")
    # Both chains are monotone after viewport clipping: y and x endpoints
    # fit in bytes. Only the interpolated x coordinate needs Q8 precision.
    for chain in range(2):
        routine.set("p", "top_point")
        edge, wrap, selected, horizontal, nonzero, positive, stepped, done = (
            routine.label() for _ in range(8)
        )
        routine.mark(edge)
        routine.expr("p")
        routine.emit("push hl")
        routine.expr("first" if chain else "last")
        routine.emit(
            "pop de",
            "or a",
            "sbc hl,de",
            f"jr z,{wrap}",
            "ex de,hl",
            f"ld de,{-12 if chain else 12}",
            "add hl,de",
            f"jr {selected}",
        )
        routine.mark(wrap)
        routine.expr("last" if chain else "first")
        routine.mark(selected)
        routine._store("next")
        routine.emit("push iy")
        routine.expr("p")
        routine.emit("push hl", "pop iy")
        routine.expr("next")
        routine.emit(
            "ld de,8",
            "add hl,de",
            "ld a,(hl)",
            "ld c,a",
            "ld a,(iy+8)",
            "ld b,a",
            "ld a,c",
            "sub b",
            f"jp z,{horizontal}",
        )
        routine.emit("ld b,0", "ld c,a")
        routine.expr("next")
        routine.emit(
            "ld de,6", "add hl,de", "ld a,(hl)", "sub (iy+6)", "push af", "push bc"
        )
        # Preserve the slope sign while dividing its unsigned magnitude.
        routine.emit(f"jr nc,{positive}", "neg")
        routine.mark(positive)
        routine.emit(
            "ld h,a",
            "ld l,0",
            "ld de,0",
            "pop bc",
            "ld a,h",
            "or a",
            f"jr z,{stepped}",
            "call __gfx_wf_udiv",
        )
        routine.mark(stepped)
        routine.emit(
            "pop af",
            f"jr nc,{nonzero}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(nonzero)
        routine.emit(
            "ld d,h",
            "ld e,l",
            "sra h",
            "rr l",
            "ld a,(iy+6)",
            "add a,h",
            "ld h,a",
            "push hl",
            "push de",
            "ld c,(iy+8)",
        )
        routine.expr("next")
        routine.emit(
            "ld de,8",
            "add hl,de",
            "ld a,(hl)",
            "sub c",
            "ld b,a",
            "ld l,c",
            "ld h,0",
            "ld de,__gfx_solid_chain_x",
            "add hl,de",
            "push hl",
            "pop iy",
            "pop de",
            "pop hl",
        )
        if blank_spans:
            aligned, align, ready = routine.label(), routine.label(), routine.label()
            routine.mark(align)
            routine.emit(
                "ld a,c",
                f"and (ix{routine.slots['stride_mask']:+d})",
                f"cp (ix{routine.slots['stride_mask']:+d})",
                f"jr z,{aligned}",
                "add hl,de",
                "inc c",
                "inc iy",
                "dec b",
                f"jp z,{horizontal}",
                f"jr {align}",
            )
            routine.mark(aligned)
            routine.emit(
                f"ld a,(ix{routine.slots['stride']:+d})",
                "cp 1",
                f"jr z,{ready}",
                "sla e",
                "rl d",
                "cp 2",
            )
            four = routine.label()
            routine.emit(f"jr nz,{four}", "inc b", "srl b", f"jr {ready}")
            routine.mark(four)
            routine.emit(
                "sla e", "rl d", "ld a,b", "add a,3", "srl a", "srl a", "ld b,a"
            )
            routine.mark(ready)
        store, fill, swap, ordered, finish = (routine.label() for _ in range(5))
        if chain == 0:
            if blank_spans:
                routine.emit("push hl", "push de", "push bc")
                routine.expr("stride")
                routine.emit(
                    "push hl", "exx", "pop de", "exx", "pop bc", "pop de", "pop hl"
                )
            routine.mark(store)
            routine.emit("ld a,l", "add a,127", "ld a,h", "adc a,0", "ld (iy+0),a")
            if blank_spans:
                routine.emit("exx", "add iy,de", "exx")
            else:
                routine.emit("inc iy")
            routine.emit("add hl,de", f"djnz {store}")
        else:
            # Keep the framebuffer row in the alternate register bank and
            # pin four Bayer rows in IX for this edge. No per-row address or
            # shade lookup remains in the scanline kernel.
            routine.emit(
                "push ix",
                "push hl",
                "push de",
                "push bc",
                "ld l,c",
                "ld h,0",
                "add hl,hl",
                "add hl,hl",
                "ld d,h",
                "ld e,l",
                "add hl,hl",
                "add hl,de",
                "ld de,0x9340",
                "add hl,de",
                "exx",
            )
            routine.expr("shade")
            routine.emit(
                "add hl,hl",
                "add hl,hl",
                "ld de,__gfx_solid_patterns",
                "add hl,de",
                "push hl",
                "pop ix",
                "pop bc",
                "pop de",
                "pop hl",
            )
            phases = [routine.label() for _ in range(4)]
            two = [routine.label(), routine.label()]
            four = routine.label()
            finish = routine.label()
            if blank_spans:
                # IX now addresses patterns. Read the saved stride from the
                # frame pointer held on the stack, before selecting row loops.
                routine.emit(
                    "push hl",
                    "push de",
                    "push ix",
                    "ld hl,6",
                    "add hl,sp",
                    "ld e,(hl)",
                    "inc hl",
                    "ld d,(hl)",
                    "push de",
                    "pop ix",
                    f"ld a,(ix{routine.slots['stride']:+d})",
                    "pop ix",
                    "pop de",
                    "pop hl",
                    "cp 4",
                    f"jp z,{four}",
                    "cp 2",
                )
                choose = routine.label()
                routine.emit(
                    f"jr nz,{choose}",
                    "ld a,c",
                    "and 2",
                    f"jp z,{two[0]}",
                    f"jp {two[1]}",
                )
                routine.mark(choose)
            routine.emit(
                "ld a,c",
                "and 3",
                f"jp z,{phases[0]}",
                "dec a",
                f"jp z,{phases[1]}",
                "dec a",
                f"jp z,{phases[2]}",
                f"jp {phases[3]}",
            )
            variants = [(phases, list(range(4)), 1)]
            if blank_spans:
                variants.extend([(two, [1, 3], 2), ([four], [3], 4)])
            for labels, patterns, stride in variants:
                for phase, (label, pattern) in enumerate(zip(labels, patterns)):
                    routine.mark(label)
                    routine.emit(
                        "ld a,l",
                        "add a,127",
                        "ld a,h",
                        "adc a,0",
                        "exx",
                        "ld c,a",
                        "ld b,(iy+0)",
                        "ld a,b",
                        "cp c",
                    )
                    ordered = routine.label()
                    routine.emit(
                        f"jr c,{ordered}",
                        f"jr z,{ordered}",
                        "ld a,b",
                        "ld b,c",
                        "ld c,a",
                    )
                    routine.mark(ordered)
                    routine.emit(
                        f"ld d,(ix+{pattern})",
                        "push hl",
                        "call __gfx_solid_fast_span",
                        "pop hl",
                        f"ld de,{12*stride}",
                        "add hl,de",
                        "exx",
                        "add hl,de",
                    )
                    for _ in range(stride):
                        routine.emit("inc iy")
                    routine.emit("dec b", f"jp z,{finish}")
                    if phase == len(labels) - 1:
                        routine.emit(f"jp {labels[0]}")
            routine.mark(finish)
            routine.emit("pop ix")
        routine.mark(horizontal)
        routine.emit("pop iy")
        routine.set("p", "next")
        routine.expr("bottom_point")
        routine.emit("push hl")
        routine.expr("p")
        routine.emit("pop de", "or a", "sbc hl,de", f"jp nz,{edge}")
    routine.ret()
    return routine.finish()


def _draw(
    extended,
    culling,
    depth_enabled=True,
    dynamic_depth=False,
    quads=False,
    blank_spans=False,
):
    normal_offset = 6 if quads else 4
    routine = NativeRoutine(
        "solid_draw",
        ("faces", "count", "cull", "shaded"),
        ("i", "face", "j", "p", "n", "flags", "common", "v", "shade", "poly", "other"),
    )
    routine.if_(("uge", "count", 129), lambda: routine.fail(14))

    def local_light():
        for i in range(3):
            routine.write(
                add("@__gfx_solid_local_light", i * 2),
                invoke(
                    "mesh_dot",
                    "@__gfx_solid_light_vector",
                    add("@__gfx_mesh_rotation", i * 2),
                    6,
                ),
            )

    routine.if_(
        ("ne", "shaded", 0),
        lambda: routine.if_(
            ("ne", word("@__gfx_solid_direction_enabled"), 0), local_light
        ),
    )
    routine.set("face", "faces")
    routine.set("i", 0)

    def face():
        skip = routine.label()
        routine.set("n", 3)
        routine.emit("push iy")
        routine.expr("face")
        routine.emit("push hl", "pop iy", "ld b,63", "ld c,0")

        def flags(j):
            routine.emit(
                f"ld l,(iy+{j})",
                "ld h,0",
                "ld de,__gfx_solid_flags",
                "add hl,de",
                "ld a,(hl)",
                "or c",
                "ld c,a",
                "ld a,(hl)",
                "and b",
                "ld b,a",
            )

        for j in range(3):
            flags(j)
        if quads:
            triangle = routine.label()
            routine.emit("ld a,(iy+3)", "cp 255", f"jr z,{triangle}")
            flags(3)
            routine.emit(f"inc (ix{routine.slots['n']:+d})")
            routine.mark(triangle)
        routine.emit(
            f"ld (ix{routine.slots['flags']:+d}),c",
            f"ld (ix{routine.slots['flags']+1:+d}),0",
            "ld a,b",
            "or a",
        )
        visible = routine.label()
        routine.emit(f"jr z,{visible}", "pop iy", f"jp {skip}")
        routine.mark(visible)

        def vertex(j):
            routine.emit(
                f"ld l,(iy+{j})",
                "ld h,0",
                "add hl,hl",
                "add hl,hl",
                "ld d,h",
                "ld e,l",
                "add hl,hl",
                "add hl,de",
                "ld de,__gfx_wf_records",
                "add hl,de",
                f"ld de,__gfx_solid_poly_a+{j*12}",
                "ld bc,12",
                "ldir",
            )

        for j in range(3):
            vertex(j)
        if quads:
            triangle = routine.label()
            routine.emit("ld a,(iy+3)", "cp 255", f"jr z,{triangle}")
            vertex(3)
            routine.mark(triangle)
        routine.emit("pop iy")

        def cull():
            for j in range(3):
                routine.write(
                    add("@__gfx_solid_normal", j * 2),
                    invoke(
                        "mesh_dot",
                        add("@__gfx_mesh_norm_view", j * 6),
                        add("face", normal_offset),
                        2,
                    ),
                )
            routine.if_(
                (
                    "eq",
                    invoke(
                        "solid_facing", "@__gfx_solid_normal", "@__gfx_solid_poly_a"
                    ),
                    0,
                ),
                lambda: routine.emit(f"jp {skip}"),
            )

        if culling:
            routine.if_(("ne", "cull", 0), cull)
        routine.set("shade", byte(add("face", 4 if quads else 3)))

        def light():
            routine.set("v", word("@__gfx_solid_ambient"))

            def legacy():
                routine.set(
                    "v",
                    invoke(
                        "mesh_dot",
                        add("face", normal_offset),
                        "@__gfx_solid_local_light",
                        2,
                    ),
                )
                routine.if_(("negative", "v"), lambda: routine.set("v", 0))
                routine.if_(("slt", 256, "v"), lambda: routine.set("v", 256))
                routine.set(
                    "v",
                    add(
                        word("@__gfx_solid_ambient"),
                        invoke("mesh_mul", "v", sub(16, word("@__gfx_solid_ambient"))),
                    ),
                )

            routine.if_(("ne", word("@__gfx_solid_direction_enabled"), 0), legacy)
            if extended:
                routine.set("v", add("v", invoke("light_faces", "face")))
            routine.if_(("slt", 16, "v"), lambda: routine.set("v", 16))
            routine.expr(invoke("mesh_imul", "v", "shade"))
            routine.emit(
                "srl h", "rr l", "srl h", "rr l", "srl h", "rr l", "srl h", "rr l"
            )
            routine._store("shade")

        routine.if_(("ne", "shaded", 0), light)
        if blank_spans:
            colored = routine.label()
            routine.expr("shade")
            routine.emit(
                "ld a,l",
                "cp 16",
                f"jr nz,{colored}",
                "ld a,(__gfx_solid_transparent_white)",
                "or a",
                f"jp nz,{skip}",
            )
            routine.mark(colored)
        routine.set("poly", "@__gfx_solid_poly_a")
        routine.set("other", "@__gfx_solid_poly_b")

        def clip(axis, bound, keep):
            routine.set(
                "n", invoke("solid_clip", "poly", "n", "other", axis, bound, keep)
            )
            routine.if_(("ult", "n", 3), lambda: routine.emit(f"jp {skip}"))
            routine.set("v", "poly")
            routine.set("poly", "other")
            routine.set("other", "v")

        def depth_clip():
            # A plane whose outcode bit is clear cannot cut this convex face.
            # Frustum/outcode guidance: cubic.org/docs/3dclip.htm
            routine.set("common", "flags")
            for bound, keep, bit in (
                ("@__gfx_mesh_near", 1, 0),
                ("@__gfx_mesh_far", 0, 1),
            ):
                clean = routine.label()
                routine.expr("common")
                routine.emit(f"bit {bit},l", f"jp z,{clean}")
                clip(4, word(bound), keep)
                routine.mark(clean)
            routine.set("j", 0)
            routine.set("p", "poly")
            routine.set("flags", 0)

            def project():
                routine.set("v", invoke("solid_project", "p"))
                routine.expr("flags")
                routine.emit("ld b,l")
                routine.expr("v")
                routine.emit("ld a,l", "or b", "ld l,a", "ld h,0")
                routine._store("flags")
                routine.set("p", add("p", 12))
                routine.set("j", add("j", 1))

            routine.while_(("ult", "j", "n"), project)

        routine.expr("flags")
        routine.emit("ld a,l", "and 3", "ld l,a", "ld h,0")
        routine._store("v")
        routine.if_(("ne", "v", 0), depth_clip)
        for axis, bound, keep, bit in (
            (6, 0, 1, 4),
            (6, 96, 0, 8),
            (8, 0, 1, 16),
            (8, 64, 0, 32),
        ):
            routine.expr("flags")
            routine.emit("ld a,l", f"and {bit}", "ld l,a", "ld h,0")
            routine._store("v")
            routine.if_(
                ("ne", "v", 0),
                lambda axis=axis, bound=bound, keep=keep: clip(axis, bound, keep),
            )
        routine.set("j", 1)
        routine.set("p", add("poly", 12))

        def triangle():
            routine.expr(invoke("solid_triangle", "poly", "p", add("p", 12), "shade"))
            routine.set("p", add("p", 12))
            routine.set("j", add("j", 1))

        def fan():
            routine.while_(("ult", "j", sub("n", 1)), triangle)

        def flat():
            routine.expr(invoke("solid_flat_polygon", "poly", "n", "shade"))

        if not depth_enabled:
            flat()
        elif dynamic_depth:
            routine.if_(("eq", word("@__gfx_solid_depth_enabled"), 0), flat, fan)
        else:
            fan()
        routine.mark(skip)
        routine.set("face", add("face", 12 if quads else 10))
        routine.set("i", add("i", 1))

    routine.while_(("ult", "i", "count"), face)
    routine.ret()
    return routine.finish()
