"""Native stack-handler unwinding. Handler records hold SP and IX."""
EXCEPTIONS={"IndexError":(1,),"ValueError":(2,4,6,7,14),"MemoryError":(3,),"ZeroDivisionError":(5,8),"KeyError":(12,),"RuntimeError":(13,19,20,21,22),"AssertionError":(15,),"StopIteration":(16,),"TypeError":(17,),"UnboundLocalError":(18,),"NameError":(18,)}


def assembly():
    return ["""__obj_throw:
        ld (__exception_code),hl
        ld de,(__exception_handler)
        ld a,d
        or e
        jp z,__obj_throw_uncaught
        ex de,hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld (__exception_handler),de
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        push de
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        push de
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        push de
        pop ix
        pop de
        pop hl
        ex de,hl
        ld sp,hl
        ex de,hl
        jp (hl)
__obj_throw_uncaught:
        ld hl,(__exception_code)
        ld (__list_error),hl
        ld sp,(__list_main_sp)
        ld ix,(__list_main_ix)
        ret"""]
