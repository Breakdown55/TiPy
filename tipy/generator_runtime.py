"""Stackful native generators. Each instance owns its saved Z80 stack."""
from .list_runtime import Routine,add,sub,word,call


def routines():
    out=[]
    r=Routine("stack_floor",(),("active",),prefix="__obj_")
    r.set("active",word("@__gen_active"));r.if_(("ne","active",0),lambda:r.ret(add(word(add("active",28)),128)))
    r.ret(add("@__program_end",256));out.append(r.finish())
    r=Routine("gen_new",("address","params","frame"),("object","stack","size","sp","index","count"),prefix="__obj_")
    r.set("count",word(add("params",2)));r.set("size",add(add("frame",1024),add("count","count")))
    r.set("object",call("malloc",40));r.set("stack",call("malloc","size"))
    r.write("object","stack");r.write(add("object",4),"size");r.write(add("object",6),32764);r.write(add("object",8),1)
    r.write(add("object",28),"stack");r.write(add("object",30),add("stack","size"))
    r.set("sp",add("stack","size"));r.set("index",0)
    def argument():
        r.set("sp",sub("sp",2));r.write("sp",("call","__obj_read","params","index"));r.set("index",add("index",1))
    r.while_(("ult","index","count"),argument)
    for value in ("@__obj_gen_finished","address"):
        r.set("sp",sub("sp",2));r.write("sp",value)
    r.write(add("object",10),"sp")
    # A persistent bottom handler closes the generator before rethrowing on its caller.
    r.write(add("object",14),add("object",32));r.write(add("object",34),"@__obj_gen_failed")
    r.write(add("object",36),add("stack","size"));r.ret("object");out.append(r.finish())
    out.append("""__obj_gen_resume:
        ld hl,2
        add hl,sp
        ld e,(hl)
        inc hl
        ld d,(hl)
        push de
        pop iy
        ld a,(iy+24)
        or a
        jp z,__obj_gen_start
        ld hl,1
        ld (__iterator_exhausted),hl
        ld iy,0x89F0
        ld hl,0
        ret
__obj_gen_start:
        ld a,(iy+26)
        or a
        jp z,__obj_gen_enter
        ld iy,0x89F0
        ld hl,13
        jp __arr_fail
__obj_gen_enter:
        ld (iy+26),1
        ld hl,0
        add hl,sp
        ld (iy+16),l
        ld (iy+17),h
        push ix
        pop hl
        ld (iy+18),l
        ld (iy+19),h
        ld hl,(__exception_handler)
        ld (iy+20),l
        ld (iy+21),h
        ld hl,(__gen_active)
        ld (iy+22),l
        ld (iy+23),h
        push iy
        pop hl
        ld (__gen_active),hl
        ld l,(iy+14)
        ld h,(iy+15)
        ld (__exception_handler),hl
        ld l,(iy+12)
        ld h,(iy+13)
        push hl
        pop ix
        ld l,(iy+10)
        ld h,(iy+11)
        ld sp,hl
        ld iy,0x89F0
        ret""")
    out.append("""__obj_gen_yield:
        ld (__gen_value),hl
        ld iy,(__gen_active)
        ld hl,0
        add hl,sp
        ld (iy+10),l
        ld (iy+11),h
        push ix
        pop hl
        ld (iy+12),l
        ld (iy+13),h
        ld hl,(__exception_handler)
        ld (iy+14),l
        ld (iy+15),h
        ld hl,0
        ld (__iterator_exhausted),hl
        ld l,(iy+28)
        ld h,(iy+29)
        ld c,(iy+10)
        ld b,(iy+11)
__obj_gen_clear_unused:
        ld a,h
        cp b
        jp nz,__obj_gen_clear_byte
        ld a,l
        cp c
        jp z,__obj_gen_restore
__obj_gen_clear_byte:
        ld (hl),0
        inc hl
        jp __obj_gen_clear_unused
        jp __obj_gen_restore""")
    out.append("""__obj_gen_finished:
        ld hl,1
        ld (__iterator_exhausted),hl
        ld iy,(__gen_active)
        ld (iy+24),1
        ld hl,0
        ld (__gen_value),hl
        jp __obj_gen_restore""")
    out.append("""__obj_gen_failed:
        ld iy,(__gen_active)
        ld (iy+24),1
        ld hl,__obj_gen_rethrow
        ld (__gen_continuation),hl
        jp __obj_gen_restore_failed
__obj_gen_rethrow:
        ld hl,(__exception_code)
        jp __obj_throw""")
    out.append("""__obj_gen_restore:
        ld hl,0
        ld (__gen_continuation),hl
__obj_gen_restore_failed:
        ld (iy+26),0
        ld l,(iy+20)
        ld h,(iy+21)
        ld (__exception_handler),hl
        ld l,(iy+22)
        ld h,(iy+23)
        ld (__gen_active),hl
        ld l,(iy+18)
        ld h,(iy+19)
        push hl
        pop ix
        ld l,(iy+16)
        ld h,(iy+17)
        ld sp,hl
        ld a,(iy+24)
        or a
        jp z,__obj_gen_restored
        push iy
        pop hl
        ld (__gen_finished_object),hl
        ld l,(iy+0)
        ld h,(iy+1)
        push hl
        call __arr_free
        pop de
        ld hl,(__gen_finished_object)
        ld b,40
__obj_gen_clear_finished:
        ld (hl),0
        inc hl
        djnz __obj_gen_clear_finished
        ld hl,(__gen_finished_object)
        ld de,24
        add hl,de
        ld (hl),1
        ld hl,0
        ld (__gen_finished_object),hl
__obj_gen_restored:
        ld iy,0x89F0
        ld hl,(__gen_continuation)
        ld a,h
        or l
        jp nz,__obj_gen_rethrow
        ld hl,(__gen_value)
        ret""")
    return out
