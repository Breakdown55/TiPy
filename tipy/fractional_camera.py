"""Sub-unit camera translation without changing integer model coordinates."""

from .camera_math import NativeRoutine, invoke
from .list_runtime import add, sub


def routines(bits, direction_only=False, checked_coordinates=()):
    scale = 1 << bits
    r = NativeRoutine(
        f"mesh_look_q{bits}",
        ("ex", "ey", "ez", "tx", "ty", "tz"),
        ("x", "y", "z"),
    )
    invalid = r.label()
    for index, name in enumerate(("ex", "ey", "ez", "tx", "ty", "tz")):
        if index in checked_coordinates:
            continue
        r.expr(name)
        r.emit(f"call __gfx_mesh_camera_bound_q{bits}")
        r.emit(f"jp nc,{invalid}")
    for axis in "xyz":
        r.set(axis, sub("t" + axis, "e" + axis))

    # Normalize a direction, never two independently rounded positions. A
    # common reduction preserves its direction and fits mesh_look's domain.
    again, reduce, ready = (r.label() for _ in range(3))
    # Whole-unit look directions can retain the ordinary camera's fast path.
    # Fractional directions keep all their bits until the bounded reduction.
    r.emit(
        f"ld a,(ix{r.slots['x']:+d})",
        f"or (ix{r.slots['y']:+d})",
        f"or (ix{r.slots['z']:+d})",
        f"and {scale - 1}",
        f"jp nz,{again}",
    )
    for axis in "xyz":
        r.expr(axis)
        for _ in range(bits):
            r.emit("sra h", "rr l")
        r._store(axis)
    if direction_only:
        # An aligned direction is already in whole units, bounded by +/-240.
        # The private direction core accepts it without another range loop.
        r.emit(f"jp {ready}")
    r.mark(again)
    for axis in "xyz":
        r.expr(axis)
        r.emit("call __gfx_mesh_camera_direction_bound", f"jp nc,{reduce}")
    r.emit(f"jp {ready}")
    r.mark(reduce)
    for axis in "xyz":
        r.expr(axis)
        r.emit("sra h", "rr l")
        r._store(axis)
    r.emit(f"jp {again}")
    r.mark(ready)
    r.emit("ld hl,0", "push hl", "push hl", "push hl")
    for axis in "xyz":
        r.expr(axis)
        r.emit("push hl")
    r.emit("call __gfx_mesh_look", "ld hl,12", "add hl,sp", "ld sp,hl")

    r.emit("ld b,0")
    for offset, axis in enumerate("xyz"):
        r.expr("e" + axis)
        r.emit(
            "push hl",
            "ld a,l",
            f"and {scale - 1}",
            "ld l,a",
            "ld h,0",
            "or b",
            "ld b,a",
        )
        r.emit(f"ld (__gfx_mesh_vertex+{offset * 2}),hl")
        r.emit("pop hl")
        for _ in range(bits):
            r.emit("sra h", "rr l")
        r.emit(f"ld (__gfx_mesh_eye+{offset * 2}),hl")
    # Integer positions need no fractional dot products, even in a scaled call.
    r.emit(
        "ld hl,0",
        "ld (__gfx_mesh_eye_shift),hl",
        "ld (__gfx_mesh_eye_shift+2),hl",
        "ld (__gfx_mesh_eye_shift+4),hl",
        "ld a,b",
        "or a",
        f"jp z,{r.name}_return",
    )
    for axis in range(3):
        r.expr(
            invoke(
                "mesh_vertex_dot",
                add("@__gfx_mesh_basis", axis * 6),
                "@__gfx_mesh_vertex",
            )
        )
        for _ in range(bits):
            r.emit("sra h", "rr l")
        r.emit(f"ld (__gfx_mesh_eye_shift+{axis * 2}),hl")
    r.ret()
    r.mark(invalid)
    r.fail(14)
    return [
        r.finish(),
        f"""__gfx_mesh_camera_bound_q{bits}:
        ld de,{120 * scale}
        add hl,de
        ld de,{240 * scale + 1}
        or a
        sbc hl,de
        ret
""",
        """__gfx_mesh_camera_direction_bound:
        ld de,120
        add hl,de
        ld de,241
        or a
        sbc hl,de
        ret
""",
    ]
