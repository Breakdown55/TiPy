"""Allocation-free indexed models, Q8.8 model/view transforms and clipping.

Angles use 256 steps per turn; size=256 is 1x. Buffers are reused across draws.
The bounded path shares the existing reciprocal projector and edge rasterizer.
"""

import math
from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub, word


def routines(
    capacity=64,
    solid=False,
    extended=False,
    wire=True,
    dynamic_depth=False,
    no_depth=False,
    outlines=True,
    compiled_vertices=False,
    blank_spans=False,
    approximate=False,
    unit_transform=False,
    prepare_only=False,
    tight_frustum=False,
):
    need_instance = extended or solid and unit_transform
    out = ["""__gfx_mesh_ready: dw 0
__gfx_mesh_near: dw 512
__gfx_mesh_far: dw 30720
__gfx_mesh_eye: dw 0,0,80
__gfx_mesh_basis: dw 256,0,0,0,256,0,0,0,-256
__gfx_mesh_rotation: defs 18,0
__gfx_mesh_view: defs 18,0
__gfx_mesh_norm_view: defs 18,0
__gfx_mesh_center: defs 6,0
__gfx_mesh_vertex: defs 6,0
__gfx_mesh_gain: defs 512,0"""]
    if unit_transform:
        # No draw can rotate or scale the model, so this matrix is immutable.
        out[0] = out[0].replace(
            "__gfx_mesh_rotation: defs 18,0",
            "__gfx_mesh_rotation: dw 256,0,0,0,256,0,0,0,256",
        )
    out.append(
        "__gfx_mesh_sine: dw "
        + ",".join(str(round(math.sin(i * math.tau / 256) * 256)) for i in range(256))
    )
    out.append(
        "__gfx_mesh_focals: dw "
        + ",".join(
            str(round(32 / math.tan(math.radians(f) / 2) * 16)) for f in range(55, 121)
        )
    )
    routine = NativeRoutine("mesh_fov", ("fov",), ("focal", "i", "p"))
    routine.if_(("slt", "fov", 55), lambda: routine.fail(14))
    routine.if_(("slt", 120, "fov"), lambda: routine.fail(14))
    routine.set(
        "focal", word(add("@__gfx_mesh_focals", add(sub("fov", 55), sub("fov", 55))))
    )
    routine.set("i", 256)
    routine.set("p", "@__gfx_mesh_gain")

    def gain():
        routine.expr("i")
        routine.emit("ld b,h", "ld c,l")
        routine.expr("focal")
        routine.emit("ld d,0", "ld e,h", "ld h,l", "ld l,0")
        for _ in range(4):
            routine.emit("add hl,hl", "rl e", "rl d")
        routine.emit("call __gfx_wf_udiv")
        routine.emit("push hl")
        routine.expr("p")
        routine.emit(
            "ex de,hl", "pop hl", "ld a,l", "ld (de),a", "inc de", "ld a,h", "ld (de),a"
        )
        routine.set("p", add("p", 2))
        routine.set("i", add("i", 1))

    routine.while_(("ult", "i", 512), gain)
    routine.write("@__gfx_mesh_ready", 1)
    routine.ret()
    out.append(routine.finish())
    routine = NativeRoutine("mesh_init", ("fov", "near", "far"))
    routine.if_(("slt", "near", 1), lambda: routine.fail(14))
    routine.if_(("sge", "near", "far"), lambda: routine.fail(14))
    routine.if_(("slt", 127, "far"), lambda: routine.fail(14))
    for name in ("near", "far"):
        routine.expr(name)
        routine.emit("ld h,l", "ld l,0")
        routine.emit(f"ld (__gfx_mesh_{name}),hl")
    routine.expr(invoke("mesh_fov", "fov"))
    routine.ret()
    out.append(routine.finish())

    routine = NativeRoutine(
        "mesh_look", ("ex", "ey", "ez", "tx", "ty", "tz"), ("x", "y", "z", "a", "b")
    )
    for axis in "xyz":
        for prefix in ("e", "t"):
            routine.if_(("slt", prefix + axis, -120), lambda: routine.fail(14))
            routine.if_(("slt", 120, prefix + axis), lambda: routine.fail(14))
        routine.set(axis, sub("t" + axis, "e" + axis))
        routine.write(add("@__gfx_mesh_eye", "xyz".index(axis) * 2), "e" + axis)
    routine.if_(
        ("eq", "x", 0), lambda: routine.if_(("eq", "z", 0), lambda: routine.fail(14))
    )
    if approximate:
        general_camera = routine.label()
        routine.expr("y")
        routine.emit("ld a,h", "or l", f"jr nz,{general_camera}")
        routine.expr(invoke("mesh_level_fast", "x", "z"))
        routine.emit("ld a,h", "or l", f"jr z,{general_camera}")
        routine.ret()
        routine.mark(general_camera)
    routine.expr(invoke("mesh_normalize", sub(0, "z"), 0, "x", "@__gfx_mesh_basis"))
    tilted = routine.label()
    routine.expr("y")
    routine.emit("ld a,h", "or l", f"jp nz,{tilted}")
    # For a level camera the forward vector is a permutation of the right
    # vector. Its length and rounding are identical; normalize only once.
    routine.write("@__gfx_mesh_basis+12", word("@__gfx_mesh_basis+4"))
    routine.write("@__gfx_mesh_basis+14", 0)
    routine.write("@__gfx_mesh_basis+16", sub(0, word("@__gfx_mesh_basis")))
    routine.write("@__gfx_mesh_basis+6", 0)
    routine.write("@__gfx_mesh_basis+10", 0)
    routine.set(
        "a", invoke("wf_qmul", word("@__gfx_mesh_basis+4"), word("@__gfx_mesh_basis+4"))
    )
    routine.set(
        "b", invoke("wf_qmul", word("@__gfx_mesh_basis"), word("@__gfx_mesh_basis"))
    )
    routine.write("@__gfx_mesh_basis+8", add("a", "b"))
    routine.ret()
    routine.mark(tilted)
    routine.expr(invoke("mesh_normalize", "x", "y", "z", add("@__gfx_mesh_basis", 12)))
    for axis, (j, k) in enumerate(((1, 2), (2, 0), (0, 1))):
        routine.set(
            "a",
            invoke(
                "wf_qmul",
                word(add("@__gfx_mesh_basis", j * 2)),
                word(add("@__gfx_mesh_basis", 12 + k * 2)),
            ),
        )
        routine.set(
            "b",
            invoke(
                "wf_qmul",
                word(add("@__gfx_mesh_basis", k * 2)),
                word(add("@__gfx_mesh_basis", 12 + j * 2)),
            ),
        )
        routine.write(add("@__gfx_mesh_basis", 6 + axis * 2), sub("a", "b"))
    routine.ret()
    out.append(routine.finish())

    routine = NativeRoutine("mesh_dot", ("a", "b", "step"), ("sum", "v"))
    routine.set("sum", 0)
    for i in range(3):
        routine.set("v", invoke("mesh_mul", word("a"), word("b")))
        routine.set("sum", add("sum", "v"))
        if i != 2:
            routine.set("a", add("a", 2))
            routine.set("b", add("b", "step"))
    routine.ret("sum")
    out.append(routine.finish())

    code = [
        "__gfx_mesh_vertex_dot:",
        "push ix",
        "push iy",
        "ld hl,8",
        "add hl,sp",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "push de",
        "pop iy",
        "ld hl,6",
        "add hl,sp",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "push de",
        "pop ix",
        "exx",
        "ld hl,0",
        "exx",
    ]
    for i in range(3):
        n = f"__gfx_mesh_vertex_dot_{i}"
        code += [
            f"ld e,(iy+{i*2})",
            f"ld d,(iy+{i*2+1})",
            "ld a,d",
            "or e",
            f"jr z,{n}_zero",
            f"ld a,(ix+{i*2})",
            "or a",
            f"jr z,{n}_zero",
            f"jp p,{n}_positive",
            "neg",
            "ld c,a",
            "xor a",
            "sub e",
            "ld e,a",
            "sbc a,a",
            "sub d",
            "ld d,a",
            "ld a,c",
            n + "_positive:",
            "ld hl,0",
            n + "_loop:",
            "srl a",
            f"jr nc,{n}_skip",
            "add hl,de",
            n + "_skip:",
            "sla e",
            "rl d",
            "or a",
            f"jr nz,{n}_loop",
            "push hl",
            "exx",
            "pop de",
            "add hl,de",
            "exx",
            n + "_zero:",
        ]
    code += ["exx", "pop iy", "pop ix", "ret"]
    out.append(
        "\n".join(line if line.endswith(":") else "        " + line for line in code)
    )

    routine = NativeRoutine("mesh_imul", ("a", "b"))
    routine.expr("a")
    routine.emit("ld a,l", "or a")
    positive, ready, loop, skip, end = (routine.label() for _ in range(5))
    routine.emit(f"jp p,{positive}", "neg", "ld c,a")
    routine.expr("b")
    routine.emit(
        "xor a",
        "sub l",
        "ld l,a",
        "sbc a,a",
        "sub h",
        "ld h,a",
        "ld a,c",
        f"jr {ready}",
    )
    routine.mark(positive)
    routine.expr("b")
    routine.mark(ready)
    routine.emit("ld d,h", "ld e,l", "ld hl,0", "or a", f"jr z,{end}")
    routine.mark(loop)
    routine.emit("srl a", f"jr nc,{skip}", "add hl,de")
    routine.mark(skip)
    routine.emit("sla e", "rl d", "or a", f"jr nz,{loop}")
    routine.mark(end)
    routine.emit(f"jp {routine.name}_return")
    out.append(routine.finish())

    # Every model/view coefficient is at most 512. Multiply the low eight
    # coefficient bits in eight iterations, with exact 1x/2x shortcuts.
    routine = NativeRoutine("mesh_mul", ("a", "b"))
    routine.expr("a")
    routine.emit("push hl")
    routine.expr("b")
    routine.emit("ex de,hl", "pop hl")
    routine.emit(
        "ld a,h",
        "xor d",
        "and 128",
        "ex af,af'",
        "bit 7,h",
        "jr z,__gfx_mesh_mul_a_positive",
        "xor a",
        "sub l",
        "ld l,a",
        "sbc a,a",
        "sub h",
        "ld h,a",
    )
    routine.mark("__gfx_mesh_mul_a_positive")
    routine.emit(
        "bit 7,d",
        "jr z,__gfx_mesh_mul_b_positive",
        "xor a",
        "sub e",
        "ld e,a",
        "sbc a,a",
        "sub d",
        "ld d,a",
    )
    routine.mark("__gfx_mesh_mul_b_positive")
    routine.emit(
        "ld a,h",
        "cp d",
        "jr c,__gfx_mesh_mul_swap",
        "jr nz,__gfx_mesh_mul_ordered",
        "ld a,l",
        "cp e",
        "jr nc,__gfx_mesh_mul_ordered",
    )
    routine.mark("__gfx_mesh_mul_swap")
    routine.emit("ex de,hl")
    routine.mark("__gfx_mesh_mul_ordered")
    routine.emit(
        "ld a,d",
        "or e",
        "jr nz,__gfx_mesh_mul_nonzero",
        "ld hl,0",
        "jr __gfx_mesh_mul_sign",
    )
    routine.mark("__gfx_mesh_mul_nonzero")
    routine.emit(
        "ld a,e",
        "or a",
        "jr nz,__gfx_mesh_mul_product",
        "ld a,d",
        "cp 1",
        "jr z,__gfx_mesh_mul_sign",
        "cp 2",
        "jr nz,__gfx_mesh_mul_product",
        "add hl,hl",
        "jr __gfx_mesh_mul_sign",
    )
    routine.mark("__gfx_mesh_mul_product")
    routine.emit("push de", "ex de,hl", "ld a,l", "ld hl,0", "ld c,0", "ld b,8")
    routine.mark("__gfx_mesh_mul_bits")
    routine.emit(
        "add hl,hl",
        "rl c",
        "sla a",
        "jr nc,__gfx_mesh_mul_skip",
        "add hl,de",
        "jr nc,__gfx_mesh_mul_skip",
        "inc c",
    )
    routine.mark("__gfx_mesh_mul_skip")
    routine.emit(
        "djnz __gfx_mesh_mul_bits",
        "ld a,l",
        "ld l,h",
        "ld h,c",
        "bit 7,a",
        "jr z,__gfx_mesh_mul_rounded",
        "inc hl",
    )
    routine.mark("__gfx_mesh_mul_rounded")
    routine.emit("pop bc", "ld a,b", "or a", "jr z,__gfx_mesh_mul_sign", "add hl,de")
    routine.mark("__gfx_mesh_mul_sign")
    routine.emit(
        "ex af,af'",
        "or a",
        f"jp z,{routine.name}_return",
        "xor a",
        "sub l",
        "ld l,a",
        "sbc a,a",
        "sub h",
        "ld h,a",
        f"jp {routine.name}_return",
    )
    out.append(routine.finish())

    routine = NativeRoutine(
        "mesh_draw",
        (
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
        ),
        (
            "sx",
            "cx",
            "sy",
            "cy",
            "sz",
            "cz",
            "a",
            "b",
            "i",
            "j",
            "p",
            "record",
            "vertices",
            "edges",
            "count",
            "radius",
        ),
    )
    routine.if_(
        ("eq", word("@__gfx_mesh_ready"), 0),
        lambda: routine.expr(invoke("mesh_init", 75, 2, 120)),
    )
    if prepare_only:
        routine.emit("xor a", "ld (__gfx_mesh_prepared),a")
    if dynamic_depth:
        routine.write("@__gfx_solid_depth_enabled", "depth_test")
    if blank_spans:
        routine.emit("xor a", "ld (__gfx_solid_transparent_white),a")
    if need_instance:
        for i, n in enumerate(("x", "y", "z", "size")):
            routine.write(add("@__gfx_mesh_instance", i * 2), n)
        routine.write("@__gfx_mesh_source", add("model", 10))
    routine.if_(("ne", word("model"), 0x4D54), lambda: routine.fail(14))
    routine.set("vertices", word(add("model", 2)))
    routine.set("edges", word(add("model", 4)))
    routine.if_(("eq", "vertices", 0), lambda: routine.fail(14))
    routine.if_(("uge", "vertices", min(capacity, 64) + 1), lambda: routine.fail(14))
    routine.if_(("eq", "edges", 0), lambda: routine.fail(14))
    routine.if_(("uge", "edges", 193), lambda: routine.fail(14))
    if not unit_transform:
        routine.if_(("slt", "size", 1), lambda: routine.fail(14))
        routine.if_(("slt", 512, "size"), lambda: routine.fail(14))
    # Reject distant instances before rotating or projecting a single vertex.
    for i, axis in enumerate("xyz"):
        routine.set(axis, sub(axis, word(add("@__gfx_mesh_eye", i * 2))))
        routine.if_(("slt", axis, -120), lambda: routine.ret())
        routine.if_(("slt", 120, axis), lambda: routine.ret())
        routine.expr(axis)
        routine.emit(f"ld (__gfx_mesh_vertex+{i*2}),hl")
    # A conservative camera-relative L1 bound guarantees safe Q8 dot products.
    routine.set("a", 0)
    for axis in "xyz":
        routine.set("b", axis)
        routine.if_(("negative", "b"), lambda: routine.set("b", sub(0, "b")))
        routine.set("a", add("a", "b"))
    routine.if_(("slt", 120, "a"), lambda: routine.ret())
    for axis in range(3):
        routine.write(
            add("@__gfx_mesh_center", axis * 2),
            invoke(
                "mesh_vertex_dot",
                add("@__gfx_mesh_basis", axis * 6),
                "@__gfx_mesh_vertex",
            ),
        )
    if unit_transform:
        routine.expr(word(add("model", 6)))
        routine.emit("ld h,l", "ld l,0")
        routine._store("radius")
    else:
        routine.set("radius", invoke("mesh_imul", word(add("model", 6)), "size"))
    routine.set("a", word(add("@__gfx_mesh_center", 4)))
    routine.if_(
        ("slt", "a", word("@__gfx_mesh_near")),
        lambda: routine.if_(
            ("ult", "radius", sub(word("@__gfx_mesh_near"), "a")), lambda: routine.ret()
        ),
    )
    routine.if_(
        ("slt", word("@__gfx_mesh_far"), "a"),
        lambda: routine.if_(
            ("ult", "radius", sub("a", word("@__gfx_mesh_far"))), lambda: routine.ret()
        ),
    )
    if tight_frustum:
        # FOV <= 75 has |x/z| < 1.25 and |y/z| < 1. The expanded
        # sphere and six-unit padding make whole-unit tests conservative.
        positive_z = routine.label()
        routine.emit(
            "ld hl,(__gfx_mesh_center+4)",
            "ld l,h",
            "ld h,0",
            "bit 7,l",
            f"jr z,{positive_z}",
            "ld h,255",
        )
        routine.mark(positive_z)
        routine.emit(
            "push hl",
            f"ld e,(ix{routine.slots['radius']+1:+d})",
            "ld d,0",
            "sla e",
            "rl d",
            "inc de",
            "inc de",
            "inc de",
            "inc de",
            "inc de",
            "inc de",
            "add hl,de",
            "ld b,h",
            "ld c,l",
            "pop hl",
            "sra h",
            "rr l",
            "sra h",
            "rr l",
            "add hl,bc",
        )
        for axis in (0, 2):
            positive = routine.label()
            if axis == 2:
                routine.emit("ld h,b", "ld l,c")
            routine.emit(
                f"ld a,(__gfx_mesh_center+{axis+1})",
                "bit 7,a",
                f"jr z,{positive}",
                "neg",
            )
            routine.mark(positive)
            routine.emit(
                "ld e,a", "ld d,0", "or a", "sbc hl,de", f"jp m,{routine.name}_return"
            )
    general, transformed = routine.label(), routine.label()
    # Unrotated unit-scale instances can reuse the camera matrix exactly.
    # This includes the common static level + translated pickup case.
    if not unit_transform:
        routine.expr("yaw")
        routine.emit("ld b,l")
        routine.expr("pitch")
        routine.emit("ld a,l", "or b", "ld b,a")
        routine.expr("roll")
        routine.emit("ld a,l", "or b", f"jp nz,{general}")
        routine.if_(("ne", "size", 256), lambda: routine.emit(f"jp {general}"))
    if not unit_transform:
        routine.write("@__gfx_mesh_identity_active", 1)
    matrix_copies = [("__gfx_mesh_basis", "__gfx_mesh_view")]
    if not unit_transform:
        matrix_copies.append(("__gfx_mesh_identity", "__gfx_mesh_rotation"))
    if solid:
        matrix_copies.append(("__gfx_mesh_basis", "__gfx_mesh_norm_view"))
    for source, target in matrix_copies:
        routine.emit(f"ld hl,{source}", f"ld de,{target}", "ld bc,18", "ldir")
    if not unit_transform:
        routine.emit(f"jp {transformed}")
        routine.mark(general)
        routine.write("@__gfx_mesh_identity_active", 0)
        for angle, axis in (("pitch", "x"), ("yaw", "y"), ("roll", "z")):
            routine.expr(angle)
            routine.emit(
                "ld h,0",
                "add hl,hl",
                "ld de,__gfx_mesh_sine",
                "add hl,de",
                "ld e,(hl)",
                "inc hl",
                "ld d,(hl)",
                "ex de,hl",
            )
            routine._store("s" + axis)
            routine.expr(angle)
            routine.emit(
                "ld a,l",
                "add a,64",
                "ld l,a",
                "ld h,0",
                "add hl,hl",
                "ld de,__gfx_mesh_sine",
                "add hl,de",
                "ld e,(hl)",
                "inc hl",
                "ld d,(hl)",
                "ex de,hl",
            )
            routine._store("c" + axis)

        def mul(a, b):
            return invoke("mesh_mul", a, b)

        coefficients = [
            mul("cz", "cy"),
            sub(mul(mul("cz", "sy"), "sx"), mul("sz", "cx")),
            add(mul(mul("cz", "sy"), "cx"), mul("sz", "sx")),
            mul("sz", "cy"),
            add(mul(mul("sz", "sy"), "sx"), mul("cz", "cx")),
            sub(mul(mul("sz", "sy"), "cx"), mul("cz", "sx")),
            sub(0, "sy"),
            mul("cy", "sx"),
            mul("cy", "cx"),
        ]
        for i, value in enumerate(coefficients):
            routine.write(add("@__gfx_mesh_rotation", i * 2), value)
        routine.set("i", 0)

        def matrix_row():
            routine.set("j", 0)

            def matrix_col():
                routine.set(
                    "a",
                    invoke(
                        "mesh_dot",
                        add("@__gfx_mesh_basis", "i"),
                        add("@__gfx_mesh_rotation", "j"),
                        6,
                    ),
                )
                # Unit basis rounding may exceed one by a few Q8 increments.
                routine.if_(("slt", 256, "a"), lambda: routine.set("a", 256))
                routine.if_(("slt", "a", -256), lambda: routine.set("a", -256))
                if solid:
                    routine.write(add("@__gfx_mesh_norm_view", add("i", "j")), "a")
                routine.write(add("@__gfx_mesh_view", add("i", "j")), mul("a", "size"))
                routine.set("j", add("j", 2))

            routine.while_(("ult", "j", 6), matrix_col)
            routine.set("i", add("i", 6))

        routine.while_(("ult", "i", 18), matrix_row)
        routine.mark(transformed)
    if compiled_vertices:
        # Specialized coordinate products are shared across every vertex of a
        # small static model, using the same general model/view matrix.
        if blank_spans:
            for i, axis in enumerate("xyz"):
                routine.expr(axis)
                routine.emit(f"ld (__gfx_mesh_relative+{i*2}),hl")
        routine.emit("ld hl,__gfx_wf_records")
        routine.expr("model")
        routine.emit("ld b,h", "ld c,l", "ld hl,__gfx_mesh_programs")
        find, next_, found, returned = (routine.label() for _ in range(4))
        routine.mark(find)
        routine.emit(
            "ld e,(hl)",
            "inc hl",
            "ld d,(hl)",
            "inc hl",
            "ld a,d",
            "or e",
            f"jp z,{routine.name}_invalid_model",
            "ld a,d",
            "xor b",
            f"jr nz,{next_}",
            "ld a,e",
            "xor c",
            f"jr z,{found}",
        )
        routine.mark(next_)
        routine.emit("inc hl", "inc hl", f"jr {find}")
        routine.mark(found)
        routine.emit(
            "ld e,(hl)",
            "inc hl",
            "ld d,(hl)",
            f"ld hl,{returned}",
            "push hl",
            "ex de,hl",
            "jp (hl)",
        )
        routine.mark(returned)
        routine.emit("ld a,h", "or l", f"jp z,{routine.name}_return")
        routine.expr("vertices")
        routine.emit(
            "add hl,hl", "ld d,h", "ld e,l", "add hl,hl", "add hl,de", "push hl"
        )
        routine.expr(add("model", 10))
        routine.emit("pop de", "add hl,de")
        routine._store("p")
    else:
        routine.set("p", add("model", 10))
        routine.set("record", "@__gfx_wf_records")
        routine.set("count", 0)

        def vertex():
            for i in range(3):
                routine.expr(word(add("p", i * 2)))
                routine.emit(f"ld (__gfx_mesh_vertex+{i*2}),hl")
            routine.set("i", 0)

            def axis():
                routine.set(
                    "a",
                    invoke(
                        "mesh_vertex_dot",
                        add("@__gfx_mesh_view", add("i", add("i", "i"))),
                        "@__gfx_mesh_vertex",
                    ),
                )
                routine.expr(word(add("@__gfx_mesh_center", "i")))
                routine.emit("push hl")
                routine.expr("a")
                routine.emit(
                    "pop de", "or a", "adc hl,de", f"jp pe,{routine.name}_return"
                )
                routine._store("a")
                routine.write(add("record", "i"), "a")
                routine.set("i", add("i", 2))

            routine.while_(("ult", "i", 6), axis)
            routine.set("p", add("p", 6))
            routine.set("record", add("record", 12))
            routine.set("count", add("count", 1))

        routine.while_(("ult", "count", "vertices"), vertex)
    routine.write("@__gfx_wf_near", word("@__gfx_mesh_near"))
    routine.write("@__gfx_wf_far", word("@__gfx_mesh_far"))
    routine.write("@__gfx_wf_gain", "@__gfx_mesh_gain")
    if prepare_only:
        routine.emit(
            "ld a,1",
            "ld (__gfx_mesh_prepared),a",
            "ld a,(__gfx_mesh_prepare_only)",
            "or a",
            f"jp nz,{routine.name}_return",
        )
    routine.emit("ld a,1", "ld (__gfx_ink),a")
    if solid:

        def filled_mesh():
            routine.expr(invoke("solid_prepare", "vertices"))
            routine.expr(
                invoke(
                    "solid_draw",
                    add("p", add("edges", "edges")),
                    word(add("model", 8)),
                    "cull",
                    "shaded",
                )
            )
            if not outlines:
                routine.ret()
                return
            routine.if_(("eq", "outline", 0), lambda: routine.ret())
            if wire:
                if not no_depth:
                    routine.expr(invoke("mesh_restore_flags", "vertices"))
                routine.expr(invoke("wf_draw_prepared", "vertices", "edges", "p"))
                routine.ret()

        routine.if_(
            ("ne", "filled", 0),
            lambda: routine.if_(("ne", word(add("model", 8)), 0), filled_mesh),
        )
    if wire:
        routine.expr(invoke("wf_draw", "vertices", "edges", "p"))
    routine.ret()
    if compiled_vertices:
        routine.mark(routine.name + "_invalid_model")
        routine.fail(14)
    out.append(routine.finish())
    out.append("__gfx_mesh_identity: dw 256,0,0,0,256,0,0,0,256")
    out.append("__gfx_mesh_identity_active: dw " + ("1" if unit_transform else "0"))
    if solid and wire and outlines and not no_depth:
        routine = NativeRoutine("mesh_restore_flags", ("count",))
        loop, advance = routine.label(), routine.label()
        routine.expr("count")
        routine.emit("ld b,l", "ld hl,__gfx_wf_records+10", "ld de,__gfx_solid_flags")
        routine.mark(loop)
        routine.emit(
            "ld a,(de)",
            "ld (hl),a",
            "inc hl",
            "ld (hl),0",
            "ld a,l",
            "add a,11",
            "ld l,a",
            f"jr nc,{advance}",
            "inc h",
        )
        routine.mark(advance)
        routine.emit("inc de", f"djnz {loop}")
        routine.ret()
        out.append(routine.finish())
    if need_instance:
        out.append("__gfx_mesh_instance: defs 8,0\n__gfx_mesh_source: dw 0")
    if blank_spans:
        out[
            0
        ] += "\n__gfx_mesh_blank: db 0\n__gfx_solid_transparent_white: db 0\n__gfx_mesh_relative: defs 6,0"
    if approximate:
        out.append(_level_fast())
    if prepare_only:
        out[0] += "\n__gfx_mesh_prepare_only: db 0\n__gfx_mesh_prepared: db 0"
    if compiled_vertices:
        out.append(_vertex_plan())
    out.append(_normalize())
    return out


def _normalize():
    """Bounded unit vectors use an allocation-free 24-bit restoring square root."""
    from .shape_runtime import words

    routine = NativeRoutine(
        "mesh_normalize",
        ("x", "y", "z", "out"),
        ("sx", "sy", "sz", "max", "lo", "v", "shift") + words("sum", 2) + words("p", 2),
    )
    routine.set("max", 0)
    routine.set("shift", 0)
    for axis in "xyz":
        routine.set("s" + axis, 0)

        def make_axis_positive(axis=axis):
            routine.set(axis, sub(0, axis))
            routine.set("s" + axis, 1)

        routine.if_(("negative", axis), make_axis_positive)
        routine.if_(("slt", "max", axis), lambda axis=axis: routine.set("max", axis))
    routine.if_(("eq", "max", 0), lambda: routine.fail(14))
    routine.if_(("slt", 2047, "max"), lambda: routine.fail(14))

    def enlarge():
        for axis in "xyz":
            routine.set(axis, add(axis, axis))
        routine.set("max", add("max", "max"))
        routine.set("shift", add("shift", 1))

    routine.while_(("ult", "max", 1024), enlarge)
    routine.zero("sum")
    for axis in "xyz":
        routine.expr(axis)
        routine.emit("ld d,h", "ld e,l", "ld b,h", "ld c,l", "call __gfx_wf_umul")
        routine._store("p0")
        routine.emit("ex de,hl")
        routine._store("p1")
        routine.binary("sum", "sum", "p")
    routine.expr("sum0")
    routine.emit("push hl")
    routine.expr("sum1")
    routine.emit("ex de,hl", "pop hl", "call __gfx_mesh_sqrt24")
    routine._store("lo")
    for i, axis in enumerate("xyz"):
        routine.expr(axis)
        routine.emit("ld d,0", "ld e,h", "ld h,l", "ld l,0", "push hl")
        routine.expr("lo")
        routine.emit("srl h", "rr l", "ld b,h", "ld c,l", "pop hl", "add hl,bc")
        carry = routine.label()
        routine.emit(f"jr nc,{carry}", "inc de")
        routine.mark(carry)
        routine.emit("push hl")
        routine.expr("lo")
        routine.emit("ld b,h", "ld c,l", "pop hl", "call __gfx_wf_udiv")
        routine._store("v")
        routine.if_(("ne", "s" + axis, 0), lambda: routine.set("v", sub(0, "v")))
        routine.write(add("out", i * 2), "v")
    routine.expr("shift")
    routine.emit("ld b,l")
    routine.expr("lo")
    routine.emit("inc b")
    loop, ready = routine.label(), routine.label()
    routine.emit(f"jr {ready}")
    routine.mark(loop)
    routine.emit("srl h", "rr l")
    routine.mark(ready)
    routine.emit(f"djnz {loop}", f"jp {routine.name}_return")
    return routine.finish() + """
; Exact restoring square root for the bounded 24-bit squared vector length.
; DE:HL input (< 3*2048^2), HL output. No multiply or divide in the loop.
; Main/alternate BC, DE, HL are scratch; IX/IY are preserved.
__gfx_mesh_sqrt24:
        ld d,e
        ld e,h
        ld h,l
        ld l,0
        exx
        ld hl,0
        ld bc,0
        ld a,12
__gfx_mesh_sqrt24_loop:
        exx
        add hl,hl
        rl e
        rl d
        exx
        adc hl,hl
        exx
        add hl,hl
        rl e
        rl d
        exx
        adc hl,hl
        sla c
        rl b
        ld d,b
        ld e,c
        sla e
        rl d
        inc de
        or a
        sbc hl,de
        jr nc,__gfx_mesh_sqrt24_accept
        add hl,de
        jr __gfx_mesh_sqrt24_next
__gfx_mesh_sqrt24_accept:
        inc bc
__gfx_mesh_sqrt24_next:
        dec a
        jr nz,__gfx_mesh_sqrt24_loop
        ld h,b
        ld l,c
        ret"""


def _vertex_plan():
    instructions = [
        "__gfx_mesh_vertex_plan:",
        "ld (__gfx_mesh_plan_terms),hl",
        "push ix",
        "push iy",
        "push de",
        "pop iy",
        "ld ix,__gfx_wf_records",
        "ld b,a",
        "__gfx_mesh_vertex_plan_loop:",
    ]
    for row in range(3):
        instructions += ["exx", "ld hl,0", "exx"]
        for axis in range(3):
            instructions += [
                "ld l,(iy+0)",
                "inc iy",
                "ld h,0",
                "add hl,hl",
                "ld de,(__gfx_mesh_plan_terms)",
                "add hl,de",
                "ld e,(hl)",
                "inc hl",
                "ld d,(hl)",
                "push de",
                "exx",
                "pop de",
                "add hl,de",
                "exx",
            ]
        instructions += [
            "exx",
            f"ld de,(__gfx_mesh_center+{row*2})",
            "or a",
            "adc hl,de",
            "jp pe,__gfx_mesh_vertex_plan_overflow",
            f"ld (ix+{row*2}),l",
            f"ld (ix+{row*2+1}),h",
            "exx",
        ]
    instructions += [
        "ld de,12",
        "add ix,de",
        "dec b",
        "jp nz,__gfx_mesh_vertex_plan_loop",
        "ld hl,1",
        "jr __gfx_mesh_vertex_plan_done",
        "__gfx_mesh_vertex_plan_overflow:",
        "ld hl,0",
        "__gfx_mesh_vertex_plan_done:",
        "pop iy",
        "pop ix",
        "ret",
        "__gfx_mesh_plan_terms: dw 0",
    ]
    return "\n".join(
        line if line.endswith(":") or ": dw " in line else "        " + line
        for line in instructions
    )


def _level_fast():
    """Small level-camera vectors use a bounded Q15 reciprocal-root table.

    Max reciprocal rounding error is under 0.07 Q8 units; arbitrary direction
    magnitudes and tilted cameras retain the full normalizer.
    """
    routine = NativeRoutine("mesh_level_fast", ("x", "z"), ("sum", "gain"))
    for component in ("x", "z"):
        in_range = routine.label()
        routine.expr(component)
        routine.emit(
            "ld de,20", "add hl,de", "ld de,41", "or a", "sbc hl,de", f"jr c,{in_range}"
        )
        routine.ret(0)
        routine.mark(in_range)
    routine.set("sum", 0)
    for component in ("x", "z"):
        nonnegative = routine.label()
        routine.expr(component)
        routine.emit(
            "bit 7,h",
            f"jr z,{nonnegative}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(nonnegative)
        routine.emit(
            "add hl,hl",
            "ld de,__gfx_mesh_level_squares",
            "add hl,de",
            "ld e,(hl)",
            "inc hl",
            "ld d,(hl)",
        )
        routine.expr("sum")
        routine.emit("add hl,de")
        routine._store("sum")
    routine.expr("sum")
    routine.emit("ld de,192", "or a", "sbc hl,de", "ld a,h", "or a")
    in_range = routine.label()
    routine.emit(f"jr nz,{in_range}", "ld a,l", "cp 128", f"jr c,{in_range}_sum")
    routine.mark(in_range)
    routine.ret(0)
    routine.mark(in_range + "_sum")
    routine.emit(
        "add hl,hl",
        "ld de,__gfx_mesh_level_roots",
        "add hl,de",
        "ld e,(hl)",
        "inc hl",
        "ld d,(hl)",
        "ex de,hl",
    )
    routine._store("gain")
    for component, forward, right in (("x", 12, 4), ("z", 16, 0)):
        nonnegative, sign_restored = routine.label(), routine.label()
        routine.expr(component)
        routine.emit(
            "ld a,h",
            "or a",
            "push af",
            f"jp p,{nonnegative}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(nonnegative)
        routine.emit(
            "ex de,hl",
            f"ld c,(ix{routine.slots['gain']:+d})",
            f"ld b,(ix{routine.slots['gain']+1:+d})",
            "call __gfx_wf_umul",
            "ld de,64",
            "add hl,de",
            "add hl,hl",
            "ld l,h",
            "ld h,0",
            "rl h",
            "pop af",
            f"jp p,{sign_restored}",
            "xor a",
            "sub l",
            "ld l,a",
            "sbc a,a",
            "sub h",
            "ld h,a",
        )
        routine.mark(sign_restored)
        routine.emit(f"ld (__gfx_mesh_basis+{forward}),hl")
        if component == "z":
            routine.emit("xor a", "sub l", "ld l,a", "sbc a,a", "sub h", "ld h,a")
        routine.emit(f"ld (__gfx_mesh_basis+{right}),hl")
    routine.emit("ld hl,0")
    for offset in (2, 6, 10, 14):
        routine.emit(f"ld (__gfx_mesh_basis+{offset}),hl")
    routine.emit("ld hl,256", "ld (__gfx_mesh_basis+8),hl")
    routine.ret(1)
    return (
        routine.finish()
        + "\n__gfx_mesh_level_squares: dw "
        + ",".join(str(i * i) for i in range(21))
        + "\n__gfx_mesh_level_roots: dw "
        + ",".join(str(round(32768 / math.sqrt(i))) for i in range(192, 320))
    )
