"""Native text strokes and modal editing. Background bits are never cleared.

Text uses a 5x7 font with six-pixel advance. Print draws into plotSScreen;
input renders while blocked and restores its saved background before each edit.
"""

from .list_runtime import Routine, add, sub, word, byte


class TextRoutine(Routine):
    def __init__(self, name, params=(), locals_=()):
        super().__init__(name, params, locals_, prefix="__text_")

    def invoke_text(self, name, *args):
        self.expr(("call", "__text_" + name, *args))

    def store_hl(self, name):
        offset = self.slots[name]
        self.emit(f"ld (ix{offset:+d}),l", f"ld (ix{offset+1:+d}),h")

    def pixel(self, x, y, flip=False):
        self.expr(x)
        self.emit("push hl")
        self.expr(y)
        self.emit(
            "ex de,hl", "pop hl", f"call __gfx_{'flip_pixel' if flip else 'pixel_on'}"
        )


def draw():
    r = TextRoutine(
        "draw", ("pointer", "length", "x", "y"), ("origin", "index", "glyph", "col")
    )
    r.set("origin", "x")
    r.set("index", 0)

    def character():
        r.set("glyph", byte(add("pointer", "index")))

        def newline():
            r.set("x", "origin")
            r.set("y", add("y", 8))

        def glyph():
            r.if_(("ult", "glyph", 32), lambda: r.set("glyph", 63))
            r.if_(("uge", "glyph", 127), lambda: r.set("glyph", 63))
            r.expr(sub("glyph", 32))
            r.emit(
                "add hl,hl", "add hl,hl", "add hl,hl", "ld de,__text_font", "add hl,de"
            )
            r.emit("push iy", "push hl", "pop iy", "ld b,7", "ld c,0")
            row, ready = r.label(), r.label()
            r.mark(row)
            r.emit("push bc")
            r.expr("x")
            r.emit(
                f"ld e,(ix{r.slots['y']:+d})",
                f"ld d,(ix{r.slots['y']+1:+d})",
                "ld a,e",
                "add a,c",
                "ld e,a",
                f"jr nc,{ready}",
                "inc d",
            )
            r.mark(ready)
            r.emit(
                "ld a,(iy+0)",
                "call __text_blit_row",
                "pop bc",
                "inc iy",
                "inc c",
                f"djnz {row}",
                "pop iy",
            )
            r.set("x", add("x", 6))

        def tab():
            r.set("col", sub("x", "origin"))
            r.while_(("uge", "col", 48), lambda: r.set("col", sub("col", 48)))
            r.set("x", add("x", sub(48, "col")))

        r.if_(
            ("eq", "glyph", 10),
            newline,
            lambda: r.if_(
                ("eq", "glyph", 9),
                tab,
                lambda: r.if_(("eq", "glyph", 13), lambda: r.set("x", "origin"), glyph),
            ),
        )
        r.set("index", add("index", 1))

    r.while_(("ult", "index", "length"), character)
    r.write("@__text_end_x", "x")
    r.write("@__text_end_y", "y")
    r.ret()
    return r.finish() + _blit_row()


def _blit_row():
    """HL=x, DE=y, A=5-bit row. Transparent OR into up to two LCD bytes."""
    return """
__text_blit_row:
        or a
        ret z
        ld c,a
        ld a,d
        or a
        ret nz
        ld a,e
        cp 64
        ret nc
        ld a,h
        or a
        jr z,__text_blit_positive
        inc a
        ret nz
        ld a,l
        cp 252
        ret c
        neg
        ld b,a
        xor a
        push af
        ld a,c
        add a,a
        add a,a
        add a,a
__text_blit_left_shift:
        add a,a
        djnz __text_blit_left_shift
        ld h,a
        ld l,0
        jr __text_blit_mask_ready
__text_blit_positive:
        ld a,l
        cp 96
        ret nc
        and 7
        ld b,a
        ld a,l
        srl a
        srl a
        srl a
        push af
        ld a,c
        add a,a
        add a,a
        add a,a
        ld h,a
        ld l,0
        inc b
        jr __text_blit_shift_ready
__text_blit_shift:
        srl h
        rr l
__text_blit_shift_ready:
        djnz __text_blit_shift
__text_blit_mask_ready:
        ld b,h
        ld c,l
        ld l,e
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        pop af
        ld e,a
        ld d,0
        add hl,de
        ld de,0x9340
        add hl,de
        cp 11
        jr z,__text_blit_last_byte
        ld a,(hl)
        or b
        ld (hl),a
        ld a,c
        or a
        ret z
        inc hl
        or (hl)
        ld (hl),a
        ret
__text_blit_last_byte:
        ld a,(hl)
        or b
        ld (hl),a
        ret
"""


def number():
    code = ["""__text_number:
        ld de,__text_number_buffer
        xor a
        ld (__text_number_started),a
        bit 7,h
        jp z,__text_number_positive
        ld a,45
        ld (de),a
        inc de
        ld a,l
        cpl
        ld l,a
        ld a,h
        cpl
        ld h,a
        inc hl
__text_number_positive:"""]
    for divisor in (10000, 1000, 100, 10, 1):
        stem = f"__text_number_{divisor}"
        code.append(f"""        ld bc,{divisor}
        ld a,47
{stem}_loop:
        inc a
        or a
        sbc hl,bc
        jp nc,{stem}_loop
        add hl,bc""")
        if divisor != 1:
            code.append(f"""        ld (__text_number_digit),a
        cp 48
        jp nz,{stem}_write
        ld a,(__text_number_started)
        or a
        jp z,{stem}_skip
{stem}_write:
        ld a,1
        ld (__text_number_started),a
        ld a,(__text_number_digit)""")
        code.append("        ld (de),a\n        inc de")
        if divisor != 1:
            code.append(stem + "_skip:")
    code.append("""        ex de,hl
        ld de,__text_number_buffer
        or a
        sbc hl,de
        ld b,h
        ld c,l
        ex de,hl
        ret
__text_number_started: db 0
__text_number_digit: db 0
__text_number_buffer: defs 6,0""")
    return "\n".join(code)


def parse_integer():
    r = TextRoutine(
        "parse", ("pointer", "length"), ("negative", "index", "value", "digit")
    )
    invalid = r.label()
    r.false_jump(("ne", "length", 0), invalid)
    r.set("negative", 0)
    r.set("index", 0)
    r.set("value", 0)

    def negative():
        r.set("negative", 1)
        r.set("index", 1)
        r.false_jump(("ne", "length", 1), invalid)

    r.if_(("eq", byte("pointer"), 45), negative)

    def digit():
        r.set("digit", sub(byte(add("pointer", "index")), 48))
        r.false_jump(("ult", "digit", 10), invalid)
        r.false_jump(("ult", "value", 3277), invalid)

        def last_digit():
            r.false_jump(("ult", "digit", add(8, "negative")), invalid)

        r.if_(("eq", "value", 3276), last_digit)
        r.expr("value")
        r.emit("ld d,h", "ld e,l", "add hl,hl", "add hl,hl", "add hl,de", "add hl,hl")
        r.store_hl("value")
        r.set("value", add("value", "digit"))
        r.set("index", add("index", 1))

    r.while_(("ult", "index", "length"), digit)
    r.if_(("ne", "negative", 0), lambda: r.set("value", sub(0, "value")))
    r.expr("value")
    r.emit("or a", f"jp {r.name}_return")
    r.mark(invalid)
    r.emit("ld hl,0", "scf")
    return r.finish()


def input_routine(used):
    r = TextRoutine(
        "input",
        ("prompt", "prompt_length", "x", "y", "integer"),
        (
            "length",
            "maximum",
            "cursor",
            "ticks",
            "key",
            "alpha",
            "char",
            "index",
            "result",
        ),
    )
    r.if_(("negative", "x"), lambda: r.fail(11))
    r.if_(("negative", "y"), lambda: r.fail(11))
    r.invoke_text("draw", "prompt", "prompt_length", "x", "y")
    r.set("x", word("@__text_end_x"))
    r.set("y", word("@__text_end_y"))

    def wrap():
        r.set("x", 0)
        r.set("y", add("y", 8))

    r.if_(("uge", "x", 91), wrap)
    # A visible entry/cursor is required. Printing itself clips freely.
    r.if_(("uge", "x", 91), lambda: r.fail(11))
    r.if_(("uge", "y", 57), lambda: r.fail(11))
    r.set("maximum", 0)
    r.set("result", sub(90, "x"))

    def capacity():
        r.set("result", sub("result", 6))
        r.set("maximum", add("maximum", 1))

    r.while_(("uge", "result", 6), capacity)
    r.if_(("eq", "maximum", 0), lambda: r.fail(11))
    r.if_(
        ("ne", "integer", 0),
        lambda: r.if_(("uge", "maximum", 7), lambda: r.set("maximum", 6)),
    )
    r.emit("ld hl,0x9340", "ld de,__text_background", "ld bc,768", "ldir")
    r.set("length", 0)
    r.set("cursor", 1)
    r.set("ticks", 0)
    r.set("alpha", 0)
    repaint, poll, enter, clear, delete, alpha, character, blink = [
        r.label() for _ in range(8)
    ]
    r.mark(repaint)
    r.emit("ld hl,__text_background", "ld de,0x9340", "ld bc,768", "ldir")
    r.invoke_text("draw", "@__text_input_buffer", "length", "x", "y")

    def cursor():
        r.set("index", 0)

        def pixel():
            r.pixel(add(word("@__text_end_x"), "index"), add("y", 7), flip=True)
            r.set("index", add("index", 1))

        r.while_(("ult", "index", 5), pixel)

    r.if_(("ne", "cursor", 0), cursor)
    r.emit("call __gfx_render")
    r.mark(poll)
    r.emit(
        "push ix",
        "push iy",
        "rst 0x28",
        "dw 0x4018",
        "pop iy",
        "pop ix",
        "ld l,a",
        "ld h,0",
    )
    r.store_hl("key")
    for value, target in ((9, enter), (15, clear), (56, delete), (48, alpha)):
        r.expr("key")
        r.emit(f"ld de,{value}", "or a", "sbc hl,de", f"jp z,{target}")
    r.if_(("ne", "key", 0), lambda: r.emit(f"jp {character}"))
    r.mark(blink)
    r.emit("ld hl,10", "call __native_sleep")
    r.set("ticks", add("ticks", 1))
    r.false_jump(("eq", "ticks", 50), poll)
    r.set("ticks", 0)
    r.set("cursor", sub(1, "cursor"))
    r.emit(f"jp {repaint}")
    r.mark(clear)
    r.set("length", 0)
    r.set("cursor", 1)
    r.emit(f"jp {repaint}")
    r.mark(delete)
    r.if_(("ne", "length", 0), lambda: r.set("length", sub("length", 1)))
    r.set("cursor", 1)
    r.emit(f"jp {repaint}")
    r.mark(alpha)

    def cycle_alpha():
        r.set("alpha", add("alpha", 1))
        r.if_(("eq", "alpha", 3), lambda: r.set("alpha", 0))

    r.if_(("eq", "integer", 0), cycle_alpha)
    r.emit(f"jp {blink}")
    r.mark(character)
    r.false_jump(("ult", "length", "maximum"), blink)
    r.set("index", "@__text_keys_digits")
    r.if_(("ne", "alpha", 0), lambda: r.set("index", "@__text_keys_alpha"))
    scan, found = r.label(), r.label()
    r.mark(scan)
    r.false_jump(("ne", byte("index"), 0), blink)
    r.if_(("eq", byte("index"), "key"), lambda: r.emit(f"jp {found}"))
    r.set("index", add("index", 2))
    r.emit(f"jp {scan}")
    r.mark(found)
    r.set("char", byte(add("index", 1)))

    def lowercase():
        r.if_(
            ("uge", "char", 65),
            lambda: r.if_(("ult", "char", 91), lambda: r.set("char", add("char", 32))),
        )

    r.if_(("eq", "alpha", 2), lowercase)
    if "input_int" in used:
        valid = r.label()
        r.if_(("ne", "integer", 0), lambda: r.emit(f"jp {valid}"))
        after = r.label()
        r.emit(f"jp {after}")
        r.mark(valid)
        sign, accepted = r.label(), r.label()
        r.if_(("eq", "char", 45), lambda: r.emit(f"jp {sign}"))
        r.false_jump(("uge", "char", 48), blink)
        r.false_jump(("ult", "char", 58), blink)
        r.emit(f"jp {accepted}")
        r.mark(sign)
        r.false_jump(("eq", "length", 0), blink)
        r.mark(accepted)
        r.mark(after)
    r.write(add("@__text_input_buffer", "length"), "char", width=1)
    r.set("length", add("length", 1))
    r.set("cursor", 1)
    r.set("ticks", 0)
    r.emit(f"jp {repaint}")
    r.mark(enter)
    if "input_int" in used:

        def integer():
            r.invoke_text("parse", "@__text_input_buffer", "length")
            r.emit(f"jp c,{blink}")
            r.store_hl("result")

        if "input_str" in used:
            r.if_(("ne", "integer", 0), integer)
        else:
            integer()
    # Restore the background and accepted text, removing only the XOR cursor.
    r.emit("ld hl,__text_background", "ld de,0x9340", "ld bc,768", "ldir")
    r.invoke_text("draw", "@__text_input_buffer", "length", "x", "y")
    r.emit("call __gfx_render")
    if "input_str" in used:

        def string():
            r.expr(("call", "__arr_from_data", 1, "length", "@__text_input_buffer"))
            r.store_hl("result")

        if "input_int" in used:
            r.if_(("eq", "integer", 0), string)
        else:
            string()
    r.ret("result")
    keys = [36, 28, 20, 35, 27, 19, 34, 26, 18, 33]
    digits = list(zip(keys, "7894561230")) + [
        (11, "-"),
        (17, "-"),
        (25, "."),
        (10, "+"),
        (37, ","),
    ]
    alpha_codes = [
        47,
        39,
        31,
        46,
        38,
        30,
        22,
        14,
        45,
        37,
        29,
        21,
        13,
        44,
        36,
        28,
        20,
        12,
        43,
        35,
        27,
        19,
        11,
        42,
        34,
        26,
    ]
    letters = list(zip(alpha_codes, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")) + [
        (33, " "),
        (25, ":"),
        (18, "?"),
    ]
    tables = []
    for name, pairs in (("digits", digits), ("alpha", letters)):
        tables.append(
            f"__text_keys_{name}: db "
            + ",".join(str(v) for key, char in pairs for v in (key, ord(char)))
            + ",0,0"
        )
    return (
        r.finish()
        + "\n"
        + "\n".join(tables)
        + "\n__text_input_buffer: defs 16,0\n__text_background: defs 768,0"
    )


def assembly(used):
    from .text_font import FONT, EXTRA

    code = [draw()] if "draw" in used else []
    if "number" in used:
        code.append(number())
    if "bool" in used:
        code.append("""__text_bool:
        ld a,h
        or l
        ld hl,__text_false
        ld bc,5
        ret z
        ld hl,__text_true
        ld bc,4
        ret
__text_false: db 70,97,108,115,101
__text_true: db 84,114,117,101""")
    if "input_int" in used:
        code.append(parse_integer())
    if "input" in used:
        code.append(input_routine(used))
    if "draw" in used:
        font = {**FONT, **EXTRA}
        code.extend(["__text_end_x: dw 0", "__text_end_y: dw 0", "__text_font:"])
        for char in range(32, 127):
            code.append("        db " + ",".join(str(b) for b in [*font[chr(char)], 0]))
    return "\n".join(code)
