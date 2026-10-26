"""Streaming native reductions, including owned managed numeric values."""
from .list_runtime import Routine,add,word,call
from .types import iterator_types,managed


def routine(t,name,result,label,next_label,truth_label):
    element=iterator_types(t)[0]
    r=Routine(label,("object","start"),("accumulator","value","temporary","comparison","have"),prefix="")
    r.set("have",0);r.set("accumulator","start")
    if name=="sum" and managed(result):r.invoke("retain","accumulator")
    loop=r.label();r.mark(loop);r.set("value",("call",next_label,"object"))
    def exhausted():
        if name in {"min","max"}:r.if_(("eq","have",0),lambda:r.fail(6))
        r.ret(0 if name=="any" else 1 if name=="all" else "accumulator")
    r.if_(("ne",word("@__iterator_exhausted"),0),exhausted)
    if name in {"any","all"}:
        r.set("comparison",("call",truth_label,"value"))
        if managed(element):r.expr(("call","__obj_release","value"))
        r.if_(("ne" if name=="any" else "eq","comparison",0),lambda:r.ret(int(name=="any")))
    elif name=="sum":
        if result=="i16":r.set("accumulator",add("accumulator","value"))
        else:
            if element!=result:
                r.set("temporary",("call","__obj_f32_from_integer","value",2 if element=="i32" else 1 if element=="fixed" else 0) if result=="float" else ("call","__obj_i32_from_int","value"))
                if managed(element):r.expr(("call","__obj_release","value"))
                r.set("value","temporary")
            r.set("temporary",("call","__obj_f32_add" if result=="float" else "__obj_i32_add","accumulator","value"))
            r.invoke("release","accumulator");r.invoke("release","value");r.set("accumulator","temporary")
    else:
        first,finish=r.label(),r.label();r.if_(("eq","have",0),lambda:r.emit(f"jp {first}"))
        if element in {"i16","u8","bool","fixed"}:
            r.if_(("slt","value","accumulator") if name=="min" else ("slt","accumulator","value"),lambda:r.emit(f"jp {first}"))
        else:
            helper="__obj_f32_compare" if element=="float" else "__obj_i32_compare" if element=="i32" else "__obj_compare"
            r.set("comparison",("call",helper,"value","accumulator"))
            r.if_(("eq","comparison",-1 if name=="min" else 1),lambda:r.emit(f"jp {first}"))
        if managed(element):r.expr(("call","__obj_release","value"))
        r.emit(f"jp {finish}");r.mark(first)
        if managed(element):r.expr(("call","__obj_release","accumulator"))
        r.set("accumulator","value");r.set("have",1);r.mark(finish)
    r.emit(f"jp {loop}");return r.finish()
