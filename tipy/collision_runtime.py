"""Allocation-free player-box collision against compile-time packed rectangles."""

from .camera_math import NativeRoutine
from .list_runtime import add, sub, word


def routines():
    r = NativeRoutine("mesh_collides", ("map", "x", "z", "radius"))
    r.if_(("ne", word("map"), 0x4342), lambda: r.fail(14))
    r.if_(("slt", "radius", 0), lambda: r.fail(14))
    r.if_(("slt", 16, "radius"), lambda: r.fail(14))
    # Bounded coordinates keep expanded bounds and signed differences exact.
    for axis in ("x", "z"):
        r.if_(("slt", axis, -240), lambda: r.ret(0))
        r.if_(("slt", 240, axis), lambda: r.ret(0))
    r.expr(word(add("map", 2)))
    valid, empty, loop, next_box = (r.label() for _ in range(4))
    r.emit("ld a,h", "or a", f"jr nz,{empty}", "ld a,l", "cp 65", f"jr c,{valid}")
    r.mark(empty)
    r.fail(14)
    r.mark(valid)
    r.emit("ld b,l", "or a")
    nonempty = r.label()
    r.emit(f"jr nz,{nonempty}")
    r.ret(0)
    r.mark(nonempty)
    r.expr(add("map", 4))
    r.emit("push iy", "push hl", "pop iy")
    r.mark(loop)
    for offset, axis, minimum in (
        (0, "x", True),
        (4, "x", False),
        (2, "z", True),
        (6, "z", False),
    ):
        radius = r.slots["radius"]
        coordinate = r.slots[axis]
        r.emit(
            f"ld l,(iy+{offset})",
            f"ld h,(iy+{offset + 1})",
            f"ld e,(ix{radius:+d})",
            f"ld d,(ix{radius + 1:+d})",
        )
        if minimum:
            r.emit("or a", "sbc hl,de")
        else:
            r.emit("add hl,de")
        r.emit(f"ld e,(ix{coordinate:+d})", f"ld d,(ix{coordinate + 1:+d})")
        if minimum:
            r.emit("ex de,hl")
        # Both operands are bounded by 256, so the sign test cannot overflow.
        r.emit("or a", "sbc hl,de", f"jp m,{next_box}")
    r.emit("pop iy")
    r.ret(1)
    r.mark(next_box)
    r.emit("ld de,8", "add iy,de", f"djnz {loop}", "pop iy")
    r.ret(0)
    return [r.finish()]
