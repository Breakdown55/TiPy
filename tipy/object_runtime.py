"""Shared ownership for nested native containers and immutable tuple records."""

from .list_runtime import Routine, add, sub, word, byte, call


def routines(has_dict=True):
    out=[]
    for name in ("shift_left","shift_right"):
        r=Routine(name,("value","count"),prefix="__obj_")
        r.if_(("negative","count"),lambda:r.fail(14))
        r.if_(("uge","count",16),lambda:r.set("count",16))
        r.expr("count");r.emit("ld b,l");r.expr("value")
        end,loop=r.label(),r.label();r.emit("ld a,b","or a",f"jp z,{end}");r.mark(loop)
        r.emit(*(["add hl,hl"] if name=="shift_left" else ["sra h","rr l"]))
        r.emit(f"djnz {loop}");r.mark(end)
        r.emit(f"jp {r.name}_return");out.append(r.finish())
    r=Routine("power",("base","exponent"),("result",),prefix="__obj_")
    r.if_(("negative","exponent"),lambda:r.fail(14));r.set("result",1)
    # Binary exponentiation, with native 16-bit wrapping arithmetic.
    def multiply(left,right,target):
        r.expr(left);r.emit("push hl");r.expr(right);r.emit("ex de,hl","pop bc","ld hl,0","ld a,16")
        loop,skip=r.label(),r.label();r.mark(loop);r.emit("srl b","rr c",f"jp nc,{skip}","add hl,de");r.mark(skip)
        r.emit("sla e","rl d","dec a",f"jp nz,{loop}")
        offset=r.slots[target];r.emit(f"ld (ix{offset:+d}),l",f"ld (ix{offset+1:+d}),h")
    def exponent_step():
        r.expr("exponent");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}");multiply("result","base","result");r.mark(skip)
        r.expr("exponent");r.emit("srl h","rr l");offset=r.slots["exponent"];r.emit(f"ld (ix{offset:+d}),l",f"ld (ix{offset+1:+d}),h")
        multiply("base","base","base")
    r.while_(("ne","exponent",0),exponent_step);r.ret("result");out.append(r.finish())
    r=Routine("different",("a","b"),prefix="__obj_");r.ret(0);out.append(r.finish())
    r=Routine("truth",("object",),prefix="__obj_")
    r.if_(("eq","object",0),lambda:r.ret(0))
    r.if_(("eq",word(add("object",2)),0),lambda:r.ret(0));r.ret(1);out.append(r.finish())
    r=Routine("release",("object",),prefix="__obj_")
    r.if_(("eq","object",0),lambda:r.ret())
    if has_dict:
        r.if_(("eq",word(add("object",6)),32767),lambda:r.ret(("call","__dict_release","object")))
    r.invoke("release","object"); r.ret(); out.append(r.finish())

    r=Routine("tuple_new",("flags","length"),("object","size"))
    r.set("object",call("malloc",12))
    r.write("object",call("malloc",add("length","length")))
    r.write(add("object",2),"length");r.write(add("object",4),"length")
    r.write(add("object",6),32766);r.write(add("object",8),1)
    r.write(add("object",10),call("malloc","length"))
    r.invoke("copy_bytes",word(add("object",10)),"flags","length")
    r.if_(("ne","length",0),lambda:r.expr(("call","__arr_zero",word("object"),add("length","length"))))
    r.ret("object");out.append(r.finish())

    r=Routine("zero",("pointer","size"))
    r.if_(("eq","size",0),lambda:r.ret())
    r.expr("pointer");r.emit("ld (hl),0","push hl","inc hl","ex de,hl","pop hl","push hl","push de")
    r.expr(sub("size",1));r.emit("ld a,h","or l")
    end=r.label();r.emit("ld b,h","ld c,l","pop de","pop hl",f"jp z,{end}","ldir");r.mark(end)
    r.ret();out.append(r.finish())
    r=Routine("to_list",("object","width"),("result","index"))
    r.set("result",call("new","width"));r.invoke("reserve","result",word(add("object",2)));r.set("index",0)
    def collect():
        r.invoke("append","result",("call","__obj_read","object","index"));r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),collect)
    r.ret("result");out.append(r.finish())

    r=Routine("to_tuple",("object","managed"),("result","index","flags","length"))
    r.set("length",word(add("object",2)));r.set("flags",call("malloc","length"));r.set("index",0)
    def flags():r.write(add("flags","index"),"managed",1);r.set("index",add("index",1))
    r.while_(("ult","index","length"),flags)
    r.set("result",call("tuple_new","flags","length"));r.invoke("free","flags");r.set("index",0)
    def collect():
        r.invoke("tuple_set","result","index",("call","__obj_read","object","index"));r.set("index",add("index",1))
    r.while_(("ult","index","length"),collect)
    r.ret("result");out.append(r.finish())

    r=Routine("tuple_set",("object","index","value"),("pointer",))
    r.set("pointer",call("address","object","index"))
    r.if_(("ne",byte(add(word(add("object",10)),"index")),0),lambda:r.set("value",call("retain","value")))
    r.write("pointer","value");r.ret();out.append(r.finish())

    r=Routine("children_release",("object",),("index","value","flags","width"))
    r.set("width",word(add("object",6)));r.set("index",0)
    def child():
        r.set("value",word(add(word("object"),add("index","index"))))
        def drop(): r.expr(("call","__obj_release","value"))
        r.if_(("eq","width",32765),drop,lambda:r.if_(("ne",byte(add(word(add("object",10)),"index")),0),drop))
        r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),child)
    r.if_(("eq","width",32766),lambda:r.invoke("free",word(add("object",10))))
    r.ret();out.append(r.finish())
    r=Routine("read",("object","index"),prefix="__obj_")
    r.if_(("eq",word(add("object",6)),1),lambda:r.ret(byte(add(word("object"),"index"))))
    r.ret(word(add(word("object"),add("index","index"))));out.append(r.finish())

    r=Routine("compare",("left","right"),("index","limit","a","b","managed","comparison"),prefix="__obj_")
    r.if_(("eq","left","right"),lambda:r.ret(0))
    if has_dict:r.if_(("eq",word(add("left",6)),32767),lambda:r.fail(14))
    r.set("limit",word(add("left",2)))
    r.if_(("ult",word(add("right",2)),"limit"),lambda:r.set("limit",word(add("right",2))))
    r.set("index",0)
    def compare_element():
        r.set("a",("call","__obj_read","left","index"));r.set("b",("call","__obj_read","right","index"))
        r.set("managed",0)
        r.if_(("eq",word(add("left",6)),32765),lambda:r.set("managed",1))
        r.if_(("eq",word(add("left",6)),32766),lambda:r.set("managed",byte(add(word(add("left",10)),"index"))))
        def compare_managed():
            r.set("comparison",("call","__obj_compare","a","b"))
            r.if_(("ne","comparison",0),lambda:r.ret("comparison"))
        def compare_scalar():
            r.if_(("slt","a","b"),lambda:r.ret(-1))
            r.if_(("slt","b","a"),lambda:r.ret(1))
        r.if_(("ne","managed",0),compare_managed,compare_scalar)
        r.set("index",add("index",1))
    r.while_(("ult","index","limit"),compare_element)
    r.if_(("ult",word(add("left",2)),word(add("right",2))),lambda:r.ret(-1))
    r.if_(("ult",word(add("right",2)),word(add("left",2))),lambda:r.ret(1))
    r.ret(0);out.append(r.finish())

    r=Routine("equal",("left","right"),("comparison","entry","slot","a","b"),prefix="__obj_")
    r.if_(("eq","left","right"),lambda:r.ret(1))
    def dict_equal():
        r.if_(("ne",word(add("left",2)),word(add("right",2))),lambda:r.ret(0))
        r.set("entry",word(add("left",14)))
        def entry():
            r.set("slot",("call","__dict_find","right",word(add("entry",4))))
            r.if_(("eq","slot",0),lambda:r.ret(0))
            r.set("a",word(add("entry",6)));r.set("b",word(add("slot",6)))
            r.if_(("ne",word(add("left",12)),0),
                lambda:r.if_(("eq",("call","__obj_equal","a","b"),0),lambda:r.ret(0)),
                lambda:r.if_(("ne","a","b"),lambda:r.ret(0)))
            r.set("entry",word(add("entry",8)))
        r.while_(("ne","entry",0),entry)
        r.ret(1)
    if has_dict:r.if_(("eq",word(add("left",6)),32767),dict_equal)
    r.set("comparison",("call","__obj_compare","left","right"))
    r.if_(("eq","comparison",0),lambda:r.ret(1));r.ret(0);out.append(r.finish())
    return out
