"""Typed truth conversion shared by bool, filters and short-circuit reductions."""
from .list_runtime import Routine,add,word
from .types import is_optional,is_view,is_object,is_closure,is_iterator


def routine(t,label,ensure):
    r=Routine(label,("value",),("bits",),prefix="")
    if t=="none":r.ret(0)
    elif is_optional(t):
        r.if_(("eq","value",0),lambda:r.ret(0));r.ret(("call",ensure(t[9:-1]),("call","__obj_read","value",0)))
    elif t in {"i32","float"}:
        r.set("bits",word(add(word("value"),2)))
        if t=="float":
            r.expr("bits");r.emit("ld a,h","and 127","ld h,a");o=r.slots["bits"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        r.expr(word(word("value")));r.emit("push hl");r.expr("bits");r.emit("pop de","ld a,h","or d","ld h,a","ld a,l","or e","ld l,a")
        o=r.slots["bits"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        r.if_(("ne","bits",0),lambda:r.ret(1));r.ret(0)
    elif is_object(t) or is_closure(t) or is_iterator(t):r.ret(1)
    elif is_view(t):
        r.set("bits",("call","__dict_view_len","value"));r.if_(("ne","bits",0),lambda:r.ret(1));r.ret(0)
    else:
        value="value" if t in {"i16","u8","bool","fixed"} else word(add("value",2))
        r.if_(("ne",value,0),lambda:r.ret(1));r.ret(0)
    return r.finish()
