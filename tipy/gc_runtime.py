"""Conservative native tracing for scopes abandoned by exception unwinding.

Only exception/coroutine programs link this collector. Ordinary programs keep
their reference-counted allocator. Interior pointers and unaligned words are
roots, so raw sequence buffers and suspended native stacks remain safe.
"""
from .list_runtime import Routine,add,sub,word,call


def routines(generators=False):
    out=[]
    for name,prefix in (("retain","__arr_"),("release","__arr_"),("release","__dict_")):
        r=Routine(name,("object",),prefix=prefix);r.ret("object");out.append(r.finish())
    r=Routine("gc_mark",("value",),("block","end"),prefix="__obj_")
    r.if_(("ult","value",add("@__heap_start",4)),lambda:r.ret())
    r.if_(("uge","value","@__heap_end"),lambda:r.ret())
    r.set("block","@__heap_start")
    def block():
        r.set("end",add(add("block",4),word("block")))
        def inside():
            def mark():r.write(add("block",2),2);r.write("@__gc_changed",1)
            r.if_(("eq",word(add("block",2)),0),mark);r.ret()
        r.if_(("ult","value","end"),inside)
        r.set("block","end")
    r.while_(("ult","block","@__heap_end"),block);r.ret();out.append(r.finish())
    r=Routine("gc_scan",("start","end"),prefix="__obj_")
    r.if_(("uge","start","end"),lambda:r.ret())
    def scan():
        r.expr(("call","__obj_gc_mark",word("start")));r.set("start",add("start",1))
    r.while_(("ult","start",sub("end",1)),scan);r.ret();out.append(r.finish())
    r=Routine("gc_collect",(),("roots","block","end","next","generator","previous"),prefix="__obj_")
    r.emit("push af","push bc","push de","push hl","push iy")
    r.set("roots","@__gc_roots")
    def root():
        r.expr(("call","__obj_gc_mark",word(word("roots"))));r.set("roots",add("roots",2))
    r.while_(("ne",word("roots"),0),root)
    if generators:
        r.set("generator",word("@__gen_active"))
        def generator_stack():
            r.expr(("call","__obj_gc_scan","$sp",word(add("generator",30))))
            def caller():
                r.set("previous",word(add("generator",22)));r.set("end",65535)
                r.if_(("ne","previous",0),lambda:r.set("end",word(add("previous",30))))
                r.expr(("call","__obj_gc_scan",word(add("generator",16)),"end"));r.set("generator","previous")
            r.while_(("ne","generator",0),caller)
        r.if_(("ne","generator",0),generator_stack,lambda:r.expr(("call","__obj_gc_scan","$sp",65535)))
    else:r.expr(("call","__obj_gc_scan","$sp",65535))
    r.write("@__gc_changed",1)
    def trace():
        r.write("@__gc_changed",0);r.set("block","@__heap_start")
        def visit():
            r.set("end",add(add("block",4),word("block")))
            r.if_(("eq",word(add("block",2)),2),lambda:r.expr(("call","__obj_gc_scan",add("block",4),"end")))
            r.set("block","end")
        r.while_(("ult","block","@__heap_end"),visit)
    r.while_(("ne",word("@__gc_changed"),0),trace)
    r.set("block","@__heap_start")
    def sweep():
        r.write(add("block",2),sub(2,word(add("block",2))))
        # Marked -> allocated; allocated and already free -> free.
        r.if_(("eq",word(add("block",2)),2),lambda:r.write(add("block",2),1))
        r.set("block",add(add("block",4),word("block")))
    r.while_(("ult","block","@__heap_end"),sweep)
    r.set("block","@__heap_start")
    def coalesce():
        r.set("next",add(add("block",4),word("block")))
        def maybe_merge():
            def first_free():
                def merge():
                    r.write("block",add(add(word("block"),4),word("next")));r.set("next","block")
                r.if_(("eq",word(add("next",2)),1),merge)
            r.if_(("eq",word(add("block",2)),1),first_free)
        r.if_(("ult","next","@__heap_end"),maybe_merge);r.set("block","next")
    r.while_(("ult","block","@__heap_end"),coalesce)
    r.emit("pop iy","pop hl","pop de","pop bc","pop af")
    r.ret();out.append(r.finish())
    return out
