"""Signed i16 remainder with Python's divisor-sign convention.

HL is the dividend, DE the divisor; HL receives the remainder. BC and AF
are scratch registers. IX, IY and the caller's stack frame are preserved.
"""

MODULO = """__math_mod:
        ld a,d
        or e
        jp z,__math_mod_zero
        ld bc,0
        bit 7,h
        jp z,__math_mod_divisor
        inc b
        ld a,l
        cpl
        ld l,a
        ld a,h
        cpl
        ld h,a
        inc hl
__math_mod_divisor:
        bit 7,d
        jp z,__math_mod_loop
        inc c
        ld a,e
        cpl
        ld e,a
        ld a,d
        cpl
        ld d,a
        inc de
__math_mod_loop:
        or a
        sbc hl,de
        jp nc,__math_mod_loop
        add hl,de
        ld a,h
        or l
        ret z
        ld a,b
        xor c
        jp z,__math_mod_sign
        ex de,hl
        or a
        sbc hl,de
__math_mod_sign:
        ld a,c
        or a
        ret z
        ld a,l
        cpl
        ld l,a
        ld a,h
        cpl
        ld h,a
        inc hl
        ret
__math_mod_zero:
        ld hl,5
        ld (__list_error),hl
        ld sp,(__list_main_sp)
        ld ix,(__list_main_ix)
        ret"""

MULTIPLY = """__math_mul:
        ld b,h
        ld c,l
        ld hl,0
        ld a,16
__math_mul_loop:
        srl b
        rr c
        jp nc,__math_mul_skip
        add hl,de
__math_mul_skip:
        sla e
        rl d
        dec a
        jp nz,__math_mul_loop
        ret"""

FLOOR_DIVIDE = """__math_div:
        ld a,d
        or e
        jp z,__math_div_zero
        ld bc,0
        bit 7,h
        jp z,__math_div_divisor
        inc b
        ld a,l
        cpl
        ld l,a
        ld a,h
        cpl
        ld h,a
        inc hl
__math_div_divisor:
        bit 7,d
        jp z,__math_div_sign
        inc c
        ld a,e
        cpl
        ld e,a
        ld a,d
        cpl
        ld d,a
        inc de
__math_div_sign:
        ld a,b
        xor c
        push af
        ld bc,0
__math_div_loop:
        or a
        sbc hl,de
        jp c,__math_div_remainder
        inc bc
        jp __math_div_loop
__math_div_remainder:
        add hl,de
        pop de
        ld a,d
        or a
        jp z,__math_div_positive
        ld a,h
        or l
        jp z,__math_div_negate
        inc bc
__math_div_negate:
        ld a,c
        cpl
        ld l,a
        ld a,b
        cpl
        ld h,a
        inc hl
        ret
__math_div_positive:
        ld h,b
        ld l,c
        ret
__math_div_zero:
        ld hl,8
        ld (__list_error),hl
        ld sp,(__list_main_sp)
        ld ix,(__list_main_ix)
        ret"""
