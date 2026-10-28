"""Bounded Q8.8 wireframes: shared vertices, reciprocal projection, 16-bit clips.

Selected only for compiler-proven small, origin-camera, unit-width packets.
The reference path retains the general Q18.14 renderer. No host drawing occurs.
"""

from .camera_math import NativeRoutine, invoke
from .shape_runtime import slot, words
from .list_runtime import add, sub, word, byte


def routines(capacity, prepared_draw=False, black_only=False):
    out = [
        "__gfx_wf_records: defs "
        + str(capacity * 12)
        + ",0\n__gfx_wf_basis: defs 18,0\n__gfx_wf_near: dw 0\n__gfx_wf_far: dw 0\n__gfx_wf_gain: dw 0"
    ]
    import math

    table = [round(128 * 4096 / math.sqrt(n)) for n in range(256, 1024)]
    out.append("__gfx_wf_root_table:\n        dw " + ",".join(map(str, table)))
    # Four-way partial unrolling trades a few bytes for fewer loop branches.
    # See map.grauw.nl/articles/mult_div_shifts.php for the Z80 tradeoff.
    out.append("""__gfx_wf_umul:
        ld a,b
        or c
        jr z,__gfx_wf_umul_zero
        ld a,d
        or e
        jr z,__gfx_wf_umul_zero
        ld a,b
        cp 1
        jr nz,__gfx_wf_umul_general
        ld a,c
        or a
        jr nz,__gfx_wf_umul_general
        ld h,e
        ld l,0
        ld e,d
        ld d,0
        ret
__gfx_wf_umul_zero:
        ld hl,0
        ld de,0
        ret
__gfx_wf_umul_general:
        ld hl,0
        ld a,d
        or a
        jr nz,__gfx_wf_umul_high
        ld d,e
        ld e,0
        ld a,d
        cp 16
        jr c,__gfx_wf_umul_four
        ld a,2
        jr __gfx_wf_umul_loop
__gfx_wf_umul_four:
        sla d
        sla d
        sla d
        sla d
        ld a,1
        jr __gfx_wf_umul_loop
__gfx_wf_umul_high:
        ld a,e
        or a
        jr nz,__gfx_wf_umul_full
        ld a,d
        cp 16
        jr nc,__gfx_wf_umul_high_eight
        sla d
        sla d
        sla d
        sla d
        ld a,1
        jr __gfx_wf_umul_high_product
__gfx_wf_umul_high_eight:
        ld a,2
__gfx_wf_umul_high_product:
        call __gfx_wf_umul_loop
        ld d,e
        ld e,h
        ld h,l
        ld l,0
        ret
__gfx_wf_umul_full:
        ld a,d
        cp 16
        jr nc,__gfx_wf_umul_sixteen
        sla e
        rl d
        sla e
        rl d
        sla e
        rl d
        sla e
        rl d
        ld a,3
        jr __gfx_wf_umul_loop
__gfx_wf_umul_sixteen:
        ld a,4
__gfx_wf_umul_loop:
        add hl,hl
        rl e
        rl d
        jr nc,__gfx_wf_umul_skip0
        add hl,bc
        jr nc,__gfx_wf_umul_skip0
        inc de
__gfx_wf_umul_skip0:
        add hl,hl
        rl e
        rl d
        jr nc,__gfx_wf_umul_skip1
        add hl,bc
        jr nc,__gfx_wf_umul_skip1
        inc de
__gfx_wf_umul_skip1:
        add hl,hl
        rl e
        rl d
        jr nc,__gfx_wf_umul_skip2
        add hl,bc
        jr nc,__gfx_wf_umul_skip2
        inc de
__gfx_wf_umul_skip2:
        add hl,hl
        rl e
        rl d
        jr nc,__gfx_wf_umul_skip3
        add hl,bc
        jr nc,__gfx_wf_umul_skip3
        inc de
__gfx_wf_umul_skip3:
        dec a
        jr nz,__gfx_wf_umul_loop
        ret
__gfx_wf_udiv:
        ld a,d
        or e
        jr nz,__gfx_wf_udiv_general
        or h
        or l
        ret z
        ld a,b
        or a
        jr nz,__gfx_wf_udiv_general
        ld a,c
        cp 1
        ret z
        or a
        jr z,__gfx_wf_udiv_general
        bit 7,a
        jr nz,__gfx_wf_udiv_general
        ld e,4
__gfx_wf_udiv_small:
        add hl,hl
        rl d
        ld a,d
        sub c
        jr c,__gfx_wf_udiv_small_next0
        ld d,a
        inc l
__gfx_wf_udiv_small_next0:
        add hl,hl
        rl d
        ld a,d
        sub c
        jr c,__gfx_wf_udiv_small_next1
        ld d,a
        inc l
__gfx_wf_udiv_small_next1:
        add hl,hl
        rl d
        ld a,d
        sub c
        jr c,__gfx_wf_udiv_small_next2
        ld d,a
        inc l
__gfx_wf_udiv_small_next2:
        add hl,hl
        rl d
        ld a,d
        sub c
        jr c,__gfx_wf_udiv_small_next3
        ld d,a
        inc l
__gfx_wf_udiv_small_next3:
        dec e
        jr nz,__gfx_wf_udiv_small
        ld e,d
        ld d,0
        ret
__gfx_wf_udiv_general:
        ld a,d
        or a
        jr nz,__gfx_wf_udiv_full
        ld a,e
        cp b
        jr c,__gfx_wf_udiv_eight
        or a
        jr nz,__gfx_wf_udiv_full
        ld a,b
        or a
        jr nz,__gfx_wf_udiv_full
        ld d,0
        ld e,h
        ld h,l
        ld l,0
        srl e
        rr h
        rr l
        ld a,9
        jr __gfx_wf_udiv_loop
__gfx_wf_udiv_eight:
        ld d,e
        ld e,h
        ld h,l
        ld l,0
        ld a,8
        jr __gfx_wf_udiv_loop
__gfx_wf_udiv_full:
        ld a,16
__gfx_wf_udiv_loop:
        add hl,hl
        rl e
        rl d
        jr c,__gfx_wf_udiv_carry
        ex de,hl
        or a
        sbc hl,bc
        ex de,hl
        jr nc,__gfx_wf_udiv_bit
        ex de,hl
        add hl,bc
        ex de,hl
        jr __gfx_wf_udiv_next
__gfx_wf_udiv_carry:
        ex de,hl
        or a
        sbc hl,bc
        ex de,hl
__gfx_wf_udiv_bit:
        inc l
__gfx_wf_udiv_next:
        dec a
        jr nz,__gfx_wf_udiv_loop
        ret""")
    routine = NativeRoutine("wf_smul", ("a", "b"), ("negative",))
    routine.set("negative", 0)
    for value in ("a", "b"):

        def make_product_positive(value=value):
            routine.set(value, sub(0, value))
            routine.set("negative", sub(1, "negative"))

        routine.if_(
            ("negative", value),
            make_product_positive,
        )
    shifted, finish = routine.label(), routine.label()

    def multiply_by_256():
        routine.expr("a")
        routine.emit("ld h,l", "ld l,0", f"jp {finish}")

    routine.if_(
        ("eq", "b", 256),
        multiply_by_256,
    )
    routine.expr("a")
    routine.emit("ld e,l", "ld d,0")
    routine.expr("b")
    routine.emit("ld a,l", "ld hl,0")
    loop, skip, end = routine.label(), routine.label(), routine.label()
    routine.emit("or a", f"jr z,{end}")
    routine.mark(loop)
    routine.emit("srl a", f"jr nc,{skip}", "add hl,de")
    routine.mark(skip)
    routine.emit("sla e", "rl d", "or a", f"jr nz,{loop}")
    routine.mark(end)
    routine.mark(finish)
    routine.emit("push hl")
    routine.expr("negative")
    routine.emit("ld a,h", "or l", "pop hl")
    positive = routine.label()
    routine.emit(
        f"jr z,{positive}", "xor a", "sub l", "ld l,a", "sbc a,a", "sub h", "ld h,a"
    )
    routine.mark(positive)
    routine.emit(f"jp {routine.name}_return")
    out.append(routine.finish())

    routine = NativeRoutine("wf_qmul", ("a", "b"), ("negative", "result"))
    routine.set("negative", 0)
    for name in ("a", "b"):

        def make_coefficient_positive(name=name):
            routine.set(name, sub(0, name))
            routine.set("negative", sub(1, "negative"))

        routine.if_(
            ("negative", name),
            make_coefficient_positive,
        )
    routine.expr("a")
    routine.emit("ex de,hl")
    routine.expr("b")
    routine.emit("ld b,h", "ld c,l", "call __gfx_wf_umul", "ld bc,128", "add hl,bc")
    carry = routine.label()
    routine.emit(f"jr nc,{carry}", "inc de")
    routine.mark(carry)
    routine.emit("ld l,h", "ld h,e")
    routine._store("result")
    routine.if_(("ne", "negative", 0), lambda: routine.ret(sub(0, "result")))
    routine.ret("result")
    out.append(routine.finish())

    routine = NativeRoutine(
        "wf_normalize",
        ("x", "y", "z", "out"),
        words("sum", 2)
        + words("p", 2)
        + ("scale", "gain", "negative", "value", "result"),
    )
    rescale, again, scaled = routine.label(), routine.label(), routine.label()
    routine.mark(again)
    for name in ("x", "y", "z"):
        routine.if_(("slt", name, -2047), lambda: routine.emit(f"jp {rescale}"))
        routine.if_(("slt", 2047, name), lambda: routine.emit(f"jp {rescale}"))
    routine.emit(f"jp {scaled}")
    routine.mark(rescale)
    for name in ("x", "y", "z"):
        routine.expr(name)
        routine.emit("sra h", "rr l")
        routine._store(name)
    routine.emit(f"jp {again}")
    routine.mark(scaled)
    routine.zero("sum")
    routine.set("scale", 0)
    for name in ("x", "y", "z"):
        routine.set("value", name)
        routine.if_(
            ("negative", "value"), lambda: routine.set("value", sub(0, "value"))
        )
        routine.expr("value")
        routine.emit("ld d,h", "ld e,l", "ld b,h", "ld c,l", "call __gfx_wf_umul")
        routine._store("p0")
        routine.emit("ex de,hl")
        routine._store("p1")
        routine.binary("sum", "sum", "p")
    routine.if_(
        ("eq", "sum0", 0),
        lambda: routine.if_(("eq", "sum1", 0), lambda: routine.fail(14)),
    )
    big, reduce, ready = routine.label(), routine.label(), routine.label()
    routine.mark(big)
    routine.if_(("ne", "sum1", 0), lambda: routine.emit(f"jp {reduce}"))
    routine.if_(("ult", "sum0", 1024), lambda: routine.emit(f"jp {ready}"))
    routine.mark(reduce)
    routine.shift("sum")
    routine.shift("sum")
    routine.set("scale", add("scale", 1))
    routine.emit(f"jp {big}")
    routine.mark(ready)

    def enlarge_squared_length():
        routine.shift("sum", False)
        routine.shift("sum", False)
        routine.set("scale", sub("scale", 1))

    routine.while_(
        ("ult", "sum0", 256),
        enlarge_squared_length,
    )
    routine.set(
        "gain",
        word(add("@__gfx_wf_root_table", add(sub("sum0", 256), sub("sum0", 256)))),
    )

    def shrink():
        routine.expr("gain")
        routine.emit("srl h", "rr l")
        routine._store("gain")
        routine.set("scale", sub("scale", 1))

    routine.while_(("slt", 0, "scale"), shrink)

    def scale_small():
        for name in ("x", "y", "z"):
            routine.set(name, add(name, name))
        routine.set("scale", add("scale", 1))

    routine.while_(("negative", "scale"), scale_small)
    for i, name in enumerate(("x", "y", "z")):
        routine.set("value", name)
        routine.set("negative", 0)

        def remember_negative_component():
            routine.set("value", sub(0, "value"))
            routine.set("negative", 1)

        routine.if_(
            ("negative", "value"),
            remember_negative_component,
        )
        routine.expr("value")
        routine.emit("ex de,hl")
        routine.expr("gain")
        routine.emit("ld b,h", "ld c,l", "call __gfx_wf_umul")
        # Q11 reciprocal gain produces a signed Q8.8 unit vector.
        routine.emit("ld bc,1024", "add hl,bc")
        carry = routine.label()
        routine.emit(f"jr nc,{carry}", "inc de")
        routine.mark(carry)
        for _ in range(5):
            routine.emit("add hl,hl", "rl e", "rl d")
        routine.emit("ex de,hl")
        routine._store("result")
        routine.if_(("uge", "result", 257), lambda: routine.set("result", 256))
        routine.if_(
            ("ne", "negative", 0), lambda: routine.set("result", sub(0, "result"))
        )
        routine.write(add("out", i * 2), "result")
    routine.ret()
    out.append(routine.finish())

    routine = NativeRoutine(
        "cam_fast_look",
        ("ex", "ey", "ez", "tx", "ty", "tz", "ux", "uy", "uz"),
        ("a", "b"),
    )
    routine.if_(("ne", word("@__gfx_cam_path_mode"), 0), lambda: routine.fail(14))
    routine.expr(invoke("wf_normalize", sub(0, "tz"), 0, "tx", "@__gfx_wf_basis"))
    routine.expr(invoke("wf_normalize", "tx", "ty", "tz", add("@__gfx_wf_basis", 12)))
    # up = cross(right, forward), with rounded Q8 products.
    for axis, (j, k) in enumerate(((1, 2), (2, 0), (0, 1))):
        routine.set(
            "a",
            invoke(
                "wf_qmul",
                word(add("@__gfx_wf_basis", j * 2)),
                word(add("@__gfx_wf_basis", 12 + k * 2)),
            ),
        )
        routine.set(
            "b",
            invoke(
                "wf_qmul",
                word(add("@__gfx_wf_basis", k * 2)),
                word(add("@__gfx_wf_basis", 12 + j * 2)),
            ),
        )
        routine.expr(sub("a", "b"))
        routine._store("a")
        routine.if_(("slt", 256, "a"), lambda: routine.set("a", 256))
        routine.if_(("slt", "a", -256), lambda: routine.set("a", -256))
        routine.write(add("@__gfx_wf_basis", 6 + axis * 2), "a")
    routine.ret()
    out.append(routine.finish())

    out.append(_project())

    # Keep the signed direction in AF' across the integer kernels and the
    # original endpoint on the stack; avoid spilling intermediate words.
    routine = NativeRoutine("wf_lerp", ("a", "b", "n", "d"))
    routine.expr("a")
    routine.emit(
        "push hl",
        f"ld e,(ix{routine.slots['b']:+d})",
        f"ld d,(ix{routine.slots['b']+1:+d})",
        "ld a,h",
        "xor 128",
        "ld b,a",
        "ld a,d",
        "xor 128",
        "cp b",
        "jr c,__gfx_wf_lerp_negative",
        "jr nz,__gfx_wf_lerp_positive",
        "ld a,e",
        "cp l",
        "jr c,__gfx_wf_lerp_negative",
    )
    routine.mark("__gfx_wf_lerp_positive")
    routine.emit(
        "ex de,hl",
        "or a",
        "sbc hl,de",
        "ld a,0",
        "ex af,af'",
        "jr __gfx_wf_lerp_product",
    )
    routine.mark("__gfx_wf_lerp_negative")
    routine.emit("or a", "sbc hl,de", "ld a,1", "ex af,af'")
    routine.mark("__gfx_wf_lerp_product")
    routine.emit(
        "ex de,hl",
        f"ld c,(ix{routine.slots['n']:+d})",
        f"ld b,(ix{routine.slots['n']+1:+d})",
        "call __gfx_wf_umul",
        f"ld c,(ix{routine.slots['d']:+d})",
        f"ld b,(ix{routine.slots['d']+1:+d})",
        "call __gfx_wf_udiv",
        "ex af,af'",
        "or a",
        "jr nz,__gfx_wf_lerp_subtract",
        "pop de",
        "add hl,de",
        f"jp {routine.name}_return",
    )
    routine.mark("__gfx_wf_lerp_subtract")
    routine.emit("pop de", "ex de,hl", "or a", "sbc hl,de", f"jp {routine.name}_return")
    out.append(routine.finish())

    routine = NativeRoutine("wf_inside", ("point", "axis", "bound", "keep"))
    routine.if_(
        ("slt", word(add("point", "axis")), "bound"),
        lambda: routine.ret(sub(1, "keep")),
    )
    routine.if_(("eq", word(add("point", "axis")), "bound"), lambda: routine.ret(1))
    routine.ret("keep")
    out.append(routine.finish())

    routine = NativeRoutine(
        "wf_clip",
        ("a", "b", "axis", "bound", "keep"),
        ("ia", "ib", "outside", "inside", "n", "d", "x", "y", "other"),
    )
    for value in ("a", "b"):
        routine.expr(value)
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
            "ld a,h",
            "xor 128",
            "ld h,a",
            "ld a,d",
            "xor 128",
            "ld d,a",
            "or a",
            "sbc hl,de",
        )
        equal, done = routine.label(), routine.label()
        routine.emit(
            f"jr z,{equal}",
            "ld a,0",
            "adc a,0",
            "ld c,a",
            f"ld a,(ix{routine.slots['keep']:+d})",
            "xor c",
            "ld l,a",
            "ld h,0",
            f"jr {done}",
        )
        routine.mark(equal)
        routine.emit("ld hl,1")
        routine.mark(done)
        routine._store("i" + value)

    routine.if_(("eq", add("ia", "ib"), 0), lambda: routine.ret(0))
    routine.if_(("eq", add("ia", "ib"), 2), lambda: routine.ret(1))
    routine.set("outside", "a")
    routine.set("inside", "b")

    def swap_clip_endpoints():
        routine.set("outside", "b")
        routine.set("inside", "a")

    routine.if_(("ne", "ia", 0), swap_clip_endpoints)
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

    def interpolate(offset):
        routine.write(
            add("outside", offset),
            invoke(
                "wf_lerp",
                word(add("outside", offset)),
                word(add("inside", offset)),
                "n",
                "d",
            ),
        )

    def depth_clip():
        interpolate(0)
        interpolate(2)
        routine.write(add("outside", 10), 64)

    def interpolate_screen_position():
        routine.set("other", sub(14, "axis"))
        interpolate("other")

    routine.if_(
        ("eq", "axis", 4),
        depth_clip,
        interpolate_screen_position,
    )
    routine.write(add("outside", "axis"), "bound")
    routine.ret(1)
    out.append(routine.finish())

    out.extend(_raster(black_only))
    out.append("__gfx_wf_ink_ref: dw __gfx_ink")
    routine = NativeRoutine(
        "cam_fast_batch",
        ("data",),
        ("count", "i", "edges", "record", "index", "pa", "pb", "flags")
        + words("a", 6)
        + words("b", 6),
    )
    routine.set("record", word("data"))
    routine.set("data", add("data", 2))
    routine.set("count", word("data"))
    routine.set("edges", add("data", 8))
    routine.write("@__gfx_wf_near", word(add("data", 4)))
    routine.write("@__gfx_wf_far", word(add("data", 6)))
    routine.set("index", word(add("data", 2)))
    routine.write("@__gfx_wf_gain", add("edges", add("index", "index")))
    transformed = routine.label()
    routine.emit(f"ld hl,{transformed}", "push hl")
    routine.expr("record")
    routine.emit("jp (hl)")
    routine.mark(transformed)
    routine.expr(invoke("wf_draw", "count", "index", "edges"))
    routine.ret()
    out.append(routine.finish())
    routine = NativeRoutine(
        "wf_draw",
        ("count", "index", "edges"),
        ("i", "record", "pa", "pb", "flags") + words("a", 6) + words("b", 6),
    )
    prepared_prologue = list(routine.code[1:])
    prepared = routine.label()
    routine.set("i", 0)
    routine.set("record", "@__gfx_wf_records")

    def transform():
        routine.expr(invoke("wf_project", "record"))
        routine.set("record", add("record", 12))
        routine.set("i", add("i", 1))

    routine.while_(("ult", "i", "count"), transform)
    routine.mark(prepared)
    routine.set("i", 0)
    routine.set("count", "index")

    def edge():
        for target in ("a", "b"):
            routine.expr(byte("edges"))
            routine.emit(
                "add hl,hl",
                "add hl,hl",
                "push hl",
                "add hl,hl",
                "pop de",
                "add hl,de",
                "ld de,__gfx_wf_records",
                "add hl,de",
            )
            routine._store("record")
            routine.set("p" + target, "record")
            routine.set("edges", add("edges", 1))
        skip = routine.label()
        routine.expr(word(add("pa", 10)))
        routine.emit("ld b,l")
        routine.expr(word(add("pb", 10)))
        routine.emit(
            "ld a,l", "and b", f"jp nz,{skip}", "ld a,l", "or b", "ld l,a", "ld h,0"
        )
        routine._store("flags")
        clipped = routine.label()
        routine.emit("ld a,l", "or a", f"jp nz,{clipped}")
        for pointer in ("pa", "pb"):
            routine.expr(add(pointer, 6))
            for _ in range(2):
                routine.emit("ld e,(hl)", "inc hl", "ld d,(hl)", "inc hl", "push de")
        routine.emit("call __gfx_wf_raster", *(["pop bc"] * 4), f"jp {skip}")
        routine.mark(clipped)
        routine.load("a", "pa", 6)
        routine.load("b", "pb", 6)
        for bound, keep, bit in (("@__gfx_wf_near", 1, 0), ("@__gfx_wf_far", 0, 1)):
            clean = routine.label()
            routine.expr("flags")
            routine.emit(f"bit {bit},l", f"jp z,{clean}")
            routine.if_(
                (
                    "eq",
                    invoke("wf_clip", slot("a0"), slot("b0"), 4, word(bound), keep),
                    0,
                ),
                lambda: routine.emit(f"jp {skip}"),
            )
            routine.mark(clean)
        for target in ("a", "b"):
            routine.expr(target + "5")
            routine.emit("ld a,l", "and 67")
            clean = routine.label()
            routine.emit(f"jr z,{clean}")
            routine.expr(invoke("wf_project", slot(target + "0")))
            routine.mark(clean)
        routine.expr("a5")
        routine.emit("ld b,l")
        routine.expr("b5")
        routine.emit(
            "ld a,l", "and b", f"jp nz,{skip}", "ld a,l", "or b", "ld l,a", "ld h,0"
        )
        routine._store("flags")
        for axis, bound, keep, bit in (
            (6, 0, 1, 2),
            (6, 95, 0, 3),
            (8, 0, 1, 4),
            (8, 63, 0, 5),
        ):
            clean = routine.label()
            routine.expr("flags")
            routine.emit(f"bit {bit},l", f"jp z,{clean}")
            routine.if_(
                ("eq", invoke("wf_clip", slot("a0"), slot("b0"), axis, bound, keep), 0),
                lambda: routine.emit(f"jp {skip}"),
            )
            routine.mark(clean)
        routine.expr(invoke("wf_raster", "a3", "a4", "b3", "b4"))
        routine.mark(skip)
        routine.set("i", add("i", 1))

    routine.while_(("ult", "i", "count"), edge)
    routine.ret()
    out.append(
        routine.finish()
        + (
            "\n__gfx_wf_draw_prepared:\n"
            + "\n".join(prepared_prologue)
            + f"\n        jp {prepared}"
            if prepared_draw
            else ""
        )
    )
    return out


def _project():
    instructions = [
        "__gfx_wf_project:",
        "push ix",
        "ld hl,4",
        "add hl,sp",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "push de",
        "pop ix",
        "ld l,(ix+4)",
        "ld h,(ix+5)",
        "bit 7,h",
        "jp nz,__gfx_wf_project_near",
        "ld de,(__gfx_wf_near)",
        "or a",
        "sbc hl,de",
        "jp c,__gfx_wf_project_near",
        "ld l,(ix+4)",
        "ld h,(ix+5)",
        "ld de,(__gfx_wf_far)",
        "ex de,hl",
        "or a",
        "sbc hl,de",
        "jp c,__gfx_wf_project_far",
        "ld l,(ix+4)",
        "ld h,(ix+5)",
        "ld b,0",
        "__gfx_wf_project_normalize:",
        "ld a,h",
        "cp 2",
        "jr c,__gfx_wf_project_gain",
        "srl h",
        "rr l",
        "inc b",
        "jr __gfx_wf_project_normalize",
        "__gfx_wf_project_gain:",
        "ld h,0",
        "add hl,hl",
        "ld de,(__gfx_wf_gain)",
        "add hl,de",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "ex de,hl",
        "ld a,b",
        "or a",
        "jr z,__gfx_wf_project_gain_ready",
        "__gfx_wf_project_gain_shift:",
        "srl h",
        "rr l",
        "djnz __gfx_wf_project_gain_shift",
        "__gfx_wf_project_gain_ready:",
        "ld b,h",
        "ld c,l",
        "ld (ix+10),0",
        "ld (ix+11),0",
    ]
    for axis in (0, 2):
        n = "__gfx_wf_project_" + str(axis)
        offset = axis + 6
        instructions += [
            f"ld l,(ix+{axis})",
            f"ld h,(ix+{axis+1})",
            "ld a,h",
            "or a",
            "push af",
            f"jp p,{n}_abs",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
            n + "_abs:",
            "ex de,hl",
            "call __gfx_wf_umul",
            "bit 7,h",
            f"jr z,{n}_rounded",
            "inc de",
            n + "_rounded:",
            "ex de,hl",
            "pop af",
            f"jp p,{n}_signed",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
            n + "_signed:",
        ]
        instructions += (
            ["ld de,48", "add hl,de"]
            if axis == 0
            else ["ex de,hl", "ld hl,32", "or a", "sbc hl,de"]
        )
        instructions += [
            f"ld (ix+{offset}),l",
            f"ld (ix+{offset+1}),h",
            "bit 7,h",
            f"jr z,{n}_positive",
            "ld a,(ix+10)",
            f"or {4 if axis==0 else 16}",
            "ld (ix+10),a",
            f"jr {n}_done",
            n + "_positive:",
            "ld a,h",
            "or a",
            f"jr nz,{n}_outside",
            "ld a,l",
            f"cp {96 if axis==0 else 64}",
            f"jr c,{n}_done",
            n + "_outside:",
            "ld a,(ix+10)",
            f"or {8 if axis==0 else 32}",
            "ld (ix+10),a",
            n + "_done:",
        ]
    instructions += [
        "pop ix",
        "ret",
        "__gfx_wf_project_near:",
        "ld (ix+10),1",
        "ld (ix+11),0",
        "pop ix",
        "ret",
        "__gfx_wf_project_far:",
        "ld (ix+10),2",
        "ld (ix+11),0",
        "pop ix",
        "ret",
    ]
    return "\n".join(
        line if line.endswith(":") else "        " + line for line in instructions
    )


def _raster(black_only=False):
    routine = NativeRoutine(
        "wf_raster", ("x1", "y1", "x2", "y2"), ("dx", "dy", "sx", "sy", "major")
    )
    routine.emit(
        "ld a,(ix+10)",
        "cp (ix+6)",
        "jp z,__gfx_wf_axis_v",
        "ld a,(ix+8)",
        "cp (ix+4)",
        "jp z,__gfx_wf_axis_h",
    )
    routine.set("sx", 1)
    routine.set("sy", 1)
    routine.set("dx", sub("x2", "x1"))
    routine.set("dy", sub("y2", "y1"))

    def reverse_x_direction():
        routine.set("dx", sub(0, "dx"))
        routine.set("sx", 0)

    routine.if_(("negative", "dx"), reverse_x_direction)

    def reverse_y_direction():
        routine.set("dy", sub(0, "dy"))
        routine.set("sy", 0)

    routine.if_(("negative", "dy"), reverse_y_direction)
    routine.set("major", 0)
    routine.if_(("ult", "dx", "dy"), lambda: routine.set("major", 1))
    # Dispatch through a small table once, including the ink mode. The hot
    # loop never tests orientation or the global drawing mode.
    routine.expr("sx")
    routine.emit("ld a,l", "add a,a", "ld c,a")
    routine.expr("sy")
    routine.emit("ld a,l", "or c", "ld c,a")
    routine.expr("major")
    routine.emit(
        "ld a,l", "add a,a", "add a,a", "or c", "ld c,a", "ld a,(__gfx_ink)", "or a"
    )
    erase = routine.label()
    routine.emit(f"jr z,{erase}", "ld a,c", "or 8", "ld c,a")
    routine.mark(erase)
    routine.emit(
        "ld l,c",
        "ld h,0",
        "add hl,hl",
        "ld de,__gfx_wf_octants",
        "add hl,de",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "push de",
    )
    routine.expr("y1")
    routine.emit(
        "add hl,hl",
        "add hl,hl",
        "push hl",
        "add hl,hl",
        "pop de",
        "add hl,de",
        "ld de,0x9340",
        "add hl,de",
        "push hl",
    )
    routine.expr("x1")
    routine.emit("ld a,l", "and 7", "ld b,a", "ld a,128")
    shift, ready = routine.label(), routine.label()
    routine.emit("inc b", f"jr {ready}")
    routine.mark(shift)
    routine.emit("rrca")
    routine.mark(ready)
    routine.emit(f"djnz {shift}", "ld d,a")
    routine.expr("x1")
    routine.emit("srl l", "srl l", "srl l", "pop bc", "add hl,bc", "push hl", "push de")

    def prepare_horizontal_step():
        routine.expr("dy")
        routine.emit("ld c,l")
        routine.expr("dx")

    def prepare_vertical_step():
        routine.expr("dx")
        routine.emit("ld c,l")
        routine.expr("dy")

    routine.if_(
        ("eq", "major", 0),
        prepare_horizontal_step,
        prepare_vertical_step,
    )
    routine.emit(
        "ld e,l",
        "ld a,l",
        "srl a",
        "ld b,a",
        "ld a,l",
        "inc a",
        "ex af,af'",
        "pop hl",
        "ld d,h",
        "pop hl",
        "ret",
    )
    assembly_blocks = [routine.finish(), _axis_raster(black_only)]

    def xstep(sx, name):
        return [
            f'{"rrc" if sx else "rlc"} d',
            f"jr nc,{name}",
            f'{"inc" if sx else "dec"} hl',
            name + ":",
        ]

    def ystep(sy, name):
        return [
            "ld a,l",
            ("add a,12" if sy else "sub 12"),
            "ld l,a",
            f"jr nc,{name}",
            f'{"inc" if sy else "dec"} h',
            name + ":",
        ]

    table = []
    for ink in ([1] if black_only else range(2)):
        for major in range(2):
            for sx in range(2):
                for sy in range(2):
                    n = f"__gfx_wf_octant_{ink}{major}{sx}{sy}"
                    table.append(n)
                    instructions = (
                        [n + ":", "ld a,d"]
                        + (["or (hl)"] if ink else ["cpl", "and (hl)"])
                        + [
                            "ld (hl),a",
                            "ex af,af'",
                            "dec a",
                            f"jp z,{n}_done",
                            "ex af,af'",
                            "ld a,b",
                            "add a,c",
                            "cp e",
                            f"jr c,{n}_straight",
                            "sub e",
                            "ld b,a",
                        ]
                    )
                    instructions += (
                        xstep(sx, n + "_minor") if major else ystep(sy, n + "_minor")
                    )
                    instructions += [
                        f"jr {n}_major",
                        n + "_straight:",
                        "ld b,a",
                        n + "_major:",
                    ]
                    instructions += (
                        ystep(sy, n + "_step") if major else xstep(sx, n + "_step")
                    )
                    instructions += [
                        f"jp {n}",
                        n + "_done:",
                        "ex af,af'",
                        "jp __gfx_wf_raster_return",
                    ]
                    assembly_blocks.append(
                        "\n".join(
                            line if line.endswith(":") else "        " + line
                            for line in instructions
                        )
                    )
    if black_only:
        import re

        assembly_blocks[0] = re.sub(
            r"        ld a,\(__gfx_ink\)\n        or a\n        jr z,(__gfx_wf_raster_L[0-9]+)\n        ld a,c\n        or 8\n        ld c,a\n\1:\n",
            "",
            assembly_blocks[0],
        )
    assembly_blocks.append("__gfx_wf_octants: dw " + ",".join(table))
    return assembly_blocks


def _axis_raster(black_only=False):
    from .solid_runtime import _basic_span

    assembly = """__gfx_wf_axis_h:
        ld b,(ix+10)
        ld c,(ix+6)
        ld a,b
        cp c
        jr c,__gfx_wf_axis_h_ordered
        ld b,c
        ld c,a
__gfx_wf_axis_h_ordered:
        inc c
        ld l,(ix+8)
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld de,0x9340
        add hl,de
        ld a,(__gfx_ink)
        neg
        ld d,a
        call __gfx_wf_axis_span
        jp __gfx_wf_raster_return
__gfx_wf_axis_v:
        ld b,(ix+8)
        ld c,(ix+4)
        ld a,b
        cp c
        jr c,__gfx_wf_axis_v_ordered
        ld b,c
        ld c,a
__gfx_wf_axis_v_ordered:
        ld a,c
        sub b
        inc a
        push af
        ld l,b
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld a,(ix+10)
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
        ld de,0x9340
        add hl,de
        push hl
        ld a,(ix+10)
        and 7
        ld b,a
        inc b
        ld a,128
__gfx_wf_axis_v_mask:
        dec b
        jr z,__gfx_wf_axis_v_ready
        rrca
        jr __gfx_wf_axis_v_mask
__gfx_wf_axis_v_ready:
        ld c,a
        pop hl
        pop af
        ld b,a
        ld de,12
        ld a,(__gfx_ink)
        or a
        jr z,__gfx_wf_axis_v_off
__gfx_wf_axis_v_on:
        ld a,c
        or (hl)
        ld (hl),a
        add hl,de
        djnz __gfx_wf_axis_v_on
        jp __gfx_wf_raster_return
__gfx_wf_axis_v_off:
        ld a,c
        cpl
        and (hl)
        ld (hl),a
        add hl,de
        djnz __gfx_wf_axis_v_off
        jp __gfx_wf_raster_return
"""
    if black_only:
        assembly = assembly.replace(
            "        ld a,(__gfx_ink)\n        neg", "        ld a,255"
        )
        assembly = assembly.replace(
            "        ld a,(__gfx_ink)\n        or a\n        jr z,__gfx_wf_axis_v_off",
            "",
        )
        assembly = assembly[: assembly.index("__gfx_wf_axis_v_off:")]
    return assembly + _basic_span().replace(
        "__gfx_solid_fast_span", "__gfx_wf_axis_span"
    )
