"""Demand-linked drawing, framebuffer, view-matrix and session kernels."""


def routines(fractional_camera=False):
    result = ["""; The compiler clips these constant row ranges to the LCD bounds.
__gfx_mesh_clear_rows:
        push ix
        ld ix,0
        add ix,sp
        ld l,(ix+6)
        ld h,(ix+7)
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld de,0x9340
        add hl,de
        push hl
        ld l,(ix+4)
        ld h,(ix+5)
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        dec hl
        ld b,h
        ld c,l
        pop hl
        ld d,h
        ld e,l
        inc de
        ld (hl),0
        ldir
        pop ix
        ret"""]
    result.append("""__gfx_mesh_session_cpu: db 128
__gfx_mesh_session_begin:
        ld a,(__gfx_mesh_session_cpu)
        bit 7,a
        ret z
        in a,(0x20)
        ld (__gfx_mesh_session_cpu),a
        ld a,1
        out (0x20),a
        ret
__gfx_mesh_session_end:
        ld a,(__gfx_mesh_session_cpu)
        cp 128
        jp nc,__native_stop_frame_timer
        out (0x20),a
        ld a,128
        ld (__gfx_mesh_session_cpu),a
        jp __native_stop_frame_timer""")
    for index, axis in enumerate("xyz"):
        result.append(
            f"__gfx_mesh_eye_{axis}:\n        ld hl,(__gfx_mesh_eye+{index*2})\n        ret"
        )
    from .camera_math import NativeRoutine
    from .list_runtime import add

    parameters = ["eye_x", "eye_y", "eye_z"] + [
        f"r{row}{column}" for row in range(3) for column in range(3)
    ]
    routine = NativeRoutine("mesh_load_view", parameters)
    for index, name in enumerate(parameters):
        bound = 120 if index < 3 else 256
        routine.if_(("slt", name, -bound), lambda: routine.fail(14))
        routine.if_(("slt", bound, name), lambda: routine.fail(14))
    for index, name in enumerate(parameters):
        routine.write(
            add(
                "@__gfx_mesh_eye" if index < 3 else "@__gfx_mesh_basis",
                index * 2 if index < 3 else (index - 3) * 2,
            ),
            name,
        )
    if fractional_camera:
        for index in range(3):
            routine.write(add("@__gfx_mesh_eye_shift", index * 2), 0)
    routine.ret()
    result.append(routine.finish())
    for writing in (False, True):
        routine = NativeRoutine(
            "mesh_buffer_write" if writing else "mesh_buffer_read",
            ("index", "value") if writing else ("index",),
        )
        routine.if_(("uge", "index", 768), lambda: routine.fail(14))
        if writing:
            routine.if_(("uge", "value", 256), lambda: routine.fail(14))
        if writing:
            routine.expr("value")
            routine.emit("ld c,l")
        routine.expr(add("index", 0x9340))
        routine.emit("ld (hl),c" if writing else "ld a,(hl)")
        if not writing:
            routine.emit("ld l,a", "ld h,0")
        if writing:
            routine.ret()
        else:
            routine.emit(f"jp {routine.name}_return")
        result.append(routine.finish())
    routine = NativeRoutine("mesh_get_pixel", ("x", "y"))
    routine.if_(("uge", "x", 96), lambda: routine.ret(0))
    routine.if_(("uge", "y", 64), lambda: routine.ret(0))
    routine.expr("y")
    routine.emit(
        "add hl,hl",
        "add hl,hl",
        "ld d,h",
        "ld e,l",
        "add hl,hl",
        "add hl,de",
        "ld de,0x9340",
        "add hl,de",
        "push hl",
    )
    routine.expr("x")
    routine.emit(
        "ld a,l",
        "and 7",
        "ld b,a",
        "srl l",
        "srl l",
        "srl l",
        "ld d,0",
        "ld e,l",
        "pop hl",
        "add hl,de",
        "ld a,128",
        "inc b",
    )
    loop, ready = routine.label(), routine.label()
    routine.emit(f"jr {ready}")
    routine.mark(loop)
    routine.emit("srl a")
    routine.mark(ready)
    routine.emit(f"djnz {loop}", "and (hl)", "ld hl,0")
    routine.emit("jp z," + loop + "_done", "inc l")
    routine.mark(loop + "_done")
    routine.emit(f"jp {routine.name}_return")
    result.append(routine.finish())
    return result
