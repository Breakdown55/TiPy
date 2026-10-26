"""Immutable byte strings lowered to native buffer operations, never host code."""
from .list_runtime import Routine,add,sub,word,byte,call


def sc(name,*args):return ("call","__str_"+name,*args)


class StringRoutine(Routine):
    def __init__(self,name,params=(),locals_=()):super().__init__(name,params,locals_,prefix="__str_")
    def invoke_str(self,name,*args):self.expr(sc(name,*args))
    def store_hl(self,name):
        offset=self.slots[name];self.emit(f"ld (ix{offset:+d}),l",f"ld (ix{offset+1:+d}),h")


def routines():
    out=[]
    r=StringRoutine("get",("object","index"),("result",))
    r.set("index",call("normalize","object","index"));r.set("result",call("new",1))
    r.invoke("append","result",byte(add(word("object"),"index")));r.ret("result");out.append(r.finish())
    r=StringRoutine("ord",("object",))
    r.if_(("ne",word(add("object",2)),1),lambda:r.fail(14))
    r.ret(byte(word("object")));out.append(r.finish())
    r=StringRoutine("chr",("value",),("result",))
    r.if_(("uge","value",128),lambda:r.fail(14));r.set("result",call("new",1))
    r.invoke("append","result","value");r.ret("result");out.append(r.finish())
    for name,helper in (("number","number"),("boolean","bool")):
        r=StringRoutine(name,("value",),("pointer","length"))
        r.expr("value");r.emit(f"call __text_{helper}");r.store_hl("pointer")
        r.emit("ld h,b","ld l,c");r.store_hl("length")
        r.ret(call("from_data",1,"length","pointer"));out.append(r.finish())
    r=StringRoutine("identity",("object",));r.ret(call("retain","object"));out.append(r.finish())
    r=StringRoutine("to_list",("object",),("result","index","value"))
    r.set("result",call("new",32765));r.set("index",0)
    def character():
        r.set("value",sc("get","object","index"));r.invoke("append","result","value");r.invoke("release","value")
        r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),character)
    r.ret("result");out.append(r.finish())

    r=StringRoutine("pad",("object","width","fill","alignment","sign_aware"),("result","count","left","right","start","value"))
    r.if_(("sge",word(add("object",2)),"width"),lambda:r.ret(call("retain","object")))
    r.set("result",call("new",1));r.invoke("reserve","result","width")
    r.set("count",sub("width",word(add("object",2))));r.set("left",0);r.set("start",0)
    r.if_(("eq","alignment",1),lambda:r.set("left","count"))
    def center():
        r.expr("count");r.emit("srl h","rr l");r.store_hl("left")
    r.if_(("eq","alignment",2),center);r.set("right",sub("count","left"))
    def sign():
        r.set("value",byte(word("object")))
        emit_sign=r.label();after=r.label()
        for char in (45,43,32):r.if_(("eq","value",char),lambda:r.emit(f"jp {emit_sign}"))
        r.emit(f"jp {after}");r.mark(emit_sign);r.invoke("append","result","value");r.set("start",1);r.mark(after)
        prefix,done=r.label(),r.label()
        r.false_jump(("uge",word(add("object",2)),add("start",2)),done)
        r.false_jump(("eq",byte(add(word("object"),"start")),48),done)
        r.set("value",byte(add(word("object"),add("start",1))))
        for char in (98,66,111,79,120,88):r.if_(("eq","value",char),lambda:r.emit(f"jp {prefix}"))
        r.emit(f"jp {done}");r.mark(prefix)
        r.invoke("append","result",48);r.invoke("append","result","value");r.set("start",add("start",2));r.mark(done)
    r.if_(("ne","sign_aware",0),lambda:r.if_(("eq","fill",48),lambda:r.if_(("eq","alignment",1),sign)))
    def left():r.invoke("append","result","fill");r.set("left",sub("left",1))
    r.while_(("ne","left",0),left)
    # append_span is defined independently and linked on demand.
    r.invoke_str("append_span","result","object","start",word(add("object",2)))
    def right():r.invoke("append","result","fill");r.set("right",sub("right",1))
    r.while_(("ne","right",0),right)
    r.ret("result");out.append(r.finish())

    r=StringRoutine("sign",("object","sign"),("result",))
    r.if_(("eq",byte(word("object")),45),lambda:r.ret(call("retain","object")))
    r.set("result",call("new",1));r.invoke("append","result","sign");r.invoke("extend","result","object")
    r.ret("result");out.append(r.finish())

    r=StringRoutine("radix",("value","shift","uppercase","prefix"),("negative","result","digit","output"))
    r.set("negative",0)
    r.if_(("negative","value"),lambda:(r.set("negative",1),r.set("value",sub(0,"value"))))
    r.set("result",call("new",1));begin=r.label();r.mark(begin)
    r.expr("shift");r.emit("ld b,l","ld a,1")
    mask=r.label();r.mark(mask);r.emit("add a,a",f"djnz {mask}","dec a","ld e,a")
    r.expr("value");r.emit("ld a,l","and e","ld l,a","ld h,0");r.store_hl("digit")
    r.if_(("ult","digit",10),lambda:r.set("digit",add("digit",48)),lambda:r.if_(("ne","uppercase",0),lambda:r.set("digit",add("digit",55)),lambda:r.set("digit",add("digit",87))))
    r.invoke("append","result","digit")
    r.expr("shift");r.emit("ld b,l");r.expr("value");shift=r.label();r.mark(shift)
    r.emit("srl h","rr l",f"djnz {shift}");r.store_hl("value")
    r.if_(("ne","value",0),lambda:r.emit(f"jp {begin}"))
    r.invoke("reverse","result");r.set("output",call("new",1))
    r.if_(("ne","negative",0),lambda:r.invoke("append","output",45))
    def prefix():
        r.invoke("append","output",48);r.set("digit",120)
        r.if_(("eq","shift",1),lambda:r.set("digit",98));r.if_(("eq","shift",3),lambda:r.set("digit",111))
        r.if_(("ne","uppercase",0),lambda:r.set("digit",sub("digit",32)))
        r.invoke("append","output","digit")
    r.if_(("ne","prefix",0),prefix)
    r.invoke("extend","output","result");r.invoke("release","result");r.ret("output");out.append(r.finish())

    r=StringRoutine("repr",("object",),("result","index","value","quote","single","double","digit"))
    r.set("result",call("new",1));r.set("single",0);r.set("double",0);r.set("index",0);r.set("quote",39)
    def scan_quote():
        r.set("value",byte(add(word("object"),"index")))
        r.if_(("eq","value",39),lambda:r.set("single",1));r.if_(("eq","value",34),lambda:r.set("double",1))
        r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),scan_quote)
    r.if_(("ne","single",0),lambda:r.if_(("eq","double",0),lambda:r.set("quote",34)))
    r.invoke("append","result","quote");r.set("index",0)
    def escaped():
        r.set("value",byte(add(word("object"),"index")))
        done=r.label()
        def escape(c):r.invoke("append","result",92);r.invoke("append","result",c);r.emit(f"jp {done}")
        r.if_(("eq","value","quote"),lambda:escape("value"))
        r.if_(("eq","value",92),lambda:escape(92))
        for value,code in ((10,110),(9,116),(13,114)):
            r.if_(("eq","value",value),lambda code=code:escape(code))
        r.invoke("append","result","value");r.mark(done);r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),escaped)
    r.invoke("append","result","quote");r.ret("result");out.append(r.finish())

    r=StringRoutine("append_span",("result","object","start","stop"),("count","length"))
    r.set("count",sub("stop","start"));r.set("length",word(add("result",2)))
    r.invoke("reserve","result",add("length","count"))
    r.invoke("copy_bytes",add(word("result"),"length"),add(word("object"),"start"),"count")
    r.write(add("result",2),add("length","count"));r.ret();out.append(r.finish())

    r=StringRoutine("find",("object","needle","start","stop"),("length","size","limit","i","matched"))
    r.set("length",word(add("object",2)));r.set("size",word(add("needle",2)))
    r.if_(("negative","start"),lambda:r.set("start",add("start","length")))
    r.if_(("negative","stop"),lambda:r.set("stop",add("stop","length")))
    for field in ("start","stop"):
        r.if_(("slt",field,0),lambda field=field:r.set(field,0))
    r.if_(("slt","length","start"),lambda:r.ret(-1))
    r.if_(("slt","length","stop"),lambda:r.set("stop","length"))
    r.if_(("slt","stop","size"),lambda:r.ret(-1))
    r.set("limit",sub("stop","size"))
    def at_position():
        r.set("i",0);r.set("matched",1)
        end=r.label()
        def compare():
            r.if_(("ne",byte(add(word("object"),add("start","i"))),byte(add(word("needle"),"i"))),lambda:(r.set("matched",0),r.emit(f"jp {end}")))
            r.set("i",add("i",1))
        r.while_(("ult","i","size"),compare);r.mark(end)
        r.if_(("ne","matched",0),lambda:r.ret("start"));r.set("start",add("start",1))
    r.while_(("slt","start",add("limit",1)),at_position)
    r.ret(-1);out.append(r.finish())

    for name in ("rfind","count"):
        r=StringRoutine(name,("object","needle","start","stop"),("found","result","size"))
        r.set("result",-1 if name=="rfind" else 0);r.set("size",word(add("needle",2)))
        if name=="rfind":r.set("size",1)
        r.if_(("eq","size",0),lambda:r.set("size",1))
        begin,end=r.label(),r.label();r.mark(begin)
        r.set("found",sc("find","object","needle","start","stop"))
        r.false_jump(("sge","found",0),end)
        r.set("result","found" if name=="rfind" else add("result",1))
        r.set("start",add("found","size"));r.emit(f"jp {begin}");r.mark(end)
        r.ret("result");out.append(r.finish())
    for name,helper in (("index","find"),("rindex","rfind")):
        r=StringRoutine(name,("object","needle","start","stop"),("result",))
        r.set("result",sc(helper,"object","needle","start","stop"));r.if_(("negative","result"),lambda:r.fail(4))
        r.ret("result");out.append(r.finish())
    r=StringRoutine("contains",("needle","object"),("found",))
    r.set("found",sc("find","object","needle",0,word(add("object",2))))
    r.if_(("negative","found"),lambda:r.ret(0));r.ret(1);out.append(r.finish())

    for name in ("startswith","endswith"):
        r=StringRoutine(name,("object","needle","start","stop"),("length","size","found"))
        r.set("length",word(add("object",2)));r.set("size",word(add("needle",2)))
        for field in ("start","stop"):
            r.if_(("negative",field),lambda field=field:r.set(field,add(field,"length")))
            r.if_(("slt",field,0),lambda field=field:r.set(field,0))
        r.if_(("slt","length","start"),lambda:r.ret(0));r.if_(("slt","length","stop"),lambda:r.set("stop","length"))
        if name=="endswith":
            r.if_(("slt",sub("stop","start"),"size"),lambda:r.ret(0));r.set("start",sub("stop","size"))
        r.set("found",sc("find","object","needle","start","stop"))
        r.if_(("eq","found","start"),lambda:r.ret(1));r.ret(0);out.append(r.finish())

    r=StringRoutine("join",("separator","parts"),("result","i","part"))
    r.set("result",call("new",1));r.set("i",0)
    def join():
        r.if_(("ne","i",0),lambda:r.invoke("extend","result","separator"))
        r.set("part",word(add(word("parts"),add("i","i"))));r.invoke("extend","result","part");r.set("i",add("i",1))
    r.while_(("ult","i",word(add("parts",2))),join)
    r.ret("result");out.append(r.finish())

    for name in ("upper_char","lower_char","space_char","alpha_char","digit_char"):
        r=StringRoutine(name,("value",))
        if name=="upper_char":
            r.if_(("uge","value",97),lambda:r.if_(("ult","value",123),lambda:r.ret(sub("value",32))))
            r.ret("value")
        elif name=="lower_char":
            r.if_(("uge","value",65),lambda:r.if_(("ult","value",91),lambda:r.ret(add("value",32))))
            r.ret("value")
        else:
            if name=="space_char":
                r.if_(("eq","value",32),lambda:r.ret(1));low,high=9,14
            elif name=="digit_char":low,high=48,58
            else:
                r.if_(("uge","value",65),lambda:r.if_(("ult","value",91),lambda:r.ret(1)));low,high=97,123
            r.if_(("uge","value",low),lambda:r.if_(("ult","value",high),lambda:r.ret(1)));r.ret(0)
        out.append(r.finish())
    for name in ("upper","lower","capitalize","title","swapcase"):
        r=StringRoutine(name,("object",),("result","i","value","previous","original"))
        r.set("result",call("new",1));r.set("i",0);r.set("previous",0)
        def convert():
            r.set("value",byte(add(word("object"),"i")));r.set("original","value")
            if name in {"upper","lower"}:r.set("value",sc(name+"_char","value"))
            elif name=="capitalize":r.if_(("eq","i",0),lambda:r.set("value",sc("upper_char","value")),lambda:r.set("value",sc("lower_char","value")))
            elif name=="title":r.if_(("eq","previous",0),lambda:r.set("value",sc("upper_char","value")),lambda:r.set("value",sc("lower_char","value")))
            else:r.if_(("eq",sc("upper_char","value"),"value"),lambda:r.set("value",sc("lower_char","value")),lambda:r.set("value",sc("upper_char","value")))
            r.set("previous",sc("alpha_char","original"));r.invoke("append","result","value");r.set("i",add("i",1))
        r.while_(("ult","i",word(add("object",2))),convert)
        r.ret("result");out.append(r.finish())

    r=StringRoutine("strip",("object","chars","mode","default_chars"),("start","stop","value","match","i"))
    r.set("start",0);r.set("stop",word(add("object",2)))
    def matches():
        r.set("match",0)
        def explicit():
            r.set("i",0)
            def scan():
                r.if_(("eq",byte(add(word("chars"),"i")),"value"),lambda:r.set("match",1));r.set("i",add("i",1))
            r.while_(("ult","i",word(add("chars",2))),scan)
        r.if_(("ne","default_chars",0),lambda:r.set("match",sc("space_char","value")),explicit)
    def left():
        begin,end=r.label(),r.label();r.mark(begin);r.false_jump(("ult","start","stop"),end)
        r.set("value",byte(add(word("object"),"start")));matches();r.false_jump(("ne","match",0),end)
        r.set("start",add("start",1));r.emit(f"jp {begin}");r.mark(end)
    def right():
        begin,end=r.label(),r.label();r.mark(begin);r.false_jump(("ult","start","stop"),end)
        r.set("value",byte(add(word("object"),sub("stop",1))));matches();r.false_jump(("ne","match",0),end)
        r.set("stop",sub("stop",1));r.emit(f"jp {begin}");r.mark(end)
    r.if_(("ne","mode",2),left);r.if_(("ne","mode",1),right)
    r.ret(call("slice","object","start","stop",1,0,0));out.append(r.finish())

    r=StringRoutine("split",("object","separator","maximum","whitespace"),("result","length","size","start","stop","piece","count","value"))
    r.set("result",call("new",32765));r.set("length",word(add("object",2)));r.set("count",0);r.set("start",0)
    def collect(stop):
        r.set("piece",call("slice","object","start",stop,1,0,0));r.invoke("append","result","piece");r.invoke("release","piece")
    def explicit_split():
        r.set("size",word(add("separator",2)));r.if_(("eq","size",0),lambda:r.fail(14))
        begin,end=r.label(),r.label();r.mark(begin)
        r.if_(("sge","maximum",0),lambda:r.false_jump(("ult","count","maximum"),end))
        r.set("stop",sc("find","object","separator","start","length"));r.false_jump(("sge","stop",0),end)
        collect("stop");r.set("start",add("stop","size"));r.set("count",add("count",1));r.emit(f"jp {begin}");r.mark(end)
        collect("length")
    def whitespace_split():
        begin,end,skip,word_loop,word_end=[r.label() for _ in range(5)];r.mark(begin)
        r.false_jump(("ult","start","length"),end)
        r.set("value",byte(add(word("object"),"start")))
        r.if_(("ne",sc("space_char","value"),0),lambda:(r.set("start",add("start",1)),r.emit(f"jp {begin}")))
        r.if_(("sge","maximum",0),lambda:r.if_(("uge","count","maximum"),lambda:(collect("length"),r.emit(f"jp {end}"))))
        r.set("stop","start");r.mark(word_loop);r.false_jump(("ult","stop","length"),word_end)
        r.set("value",byte(add(word("object"),"stop")));r.if_(("ne",sc("space_char","value"),0),lambda:r.emit(f"jp {word_end}"))
        r.set("stop",add("stop",1));r.emit(f"jp {word_loop}");r.mark(word_end)
        collect("stop");r.set("start","stop");r.set("count",add("count",1));r.emit(f"jp {begin}");r.mark(end)
    r.if_(("ne","whitespace",0),whitespace_split,explicit_split)
    r.ret("result");out.append(r.finish())

    r=StringRoutine("replace",("object","old","new","maximum"),("result","start","stop","length","size","count"))
    r.set("result",call("new",1));r.set("start",0);r.set("count",0)
    r.set("length",word(add("object",2)));r.set("size",word(add("old",2)))
    def empty_old():
        begin,end=r.label(),r.label();r.mark(begin)
        r.if_(("negative","maximum"),lambda:r.invoke("extend","result","new"),lambda:r.if_(("ult","count","maximum"),lambda:r.invoke("extend","result","new")))
        r.set("count",add("count",1));r.false_jump(("ult","start","length"),end)
        r.invoke("append","result",byte(add(word("object"),"start")));r.set("start",add("start",1));r.emit(f"jp {begin}");r.mark(end)
    def nonempty():
        begin,end=r.label(),r.label();r.mark(begin)
        r.if_(("sge","maximum",0),lambda:r.false_jump(("ult","count","maximum"),end))
        r.set("stop",sc("find","object","old","start","length"));r.false_jump(("sge","stop",0),end)
        r.invoke_str("append_span","result","object","start","stop");r.invoke("extend","result","new")
        r.set("start",add("stop","size"));r.set("count",add("count",1));r.emit(f"jp {begin}");r.mark(end)
        r.invoke_str("append_span","result","object","start","length")
    r.if_(("eq","size",0),empty_old,nonempty)
    r.ret("result");out.append(r.finish())
    return out
