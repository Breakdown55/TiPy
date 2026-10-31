"""Native slicing, concatenation, repetition and stable sorting of sequences."""
from .list_runtime import Routine,add,sub,word,byte,call


def routines():
    out=[]
    r=Routine("slice",("object","start","stop","step","start_missing","stop_missing"),
              ("length","current","magnitude","count","result","flags","index","value"))
    r.set("length",word(add("object",2)))
    r.if_(("eq","step",0),lambda:r.fail(14))
    def positive():
        r.if_(("ne","start_missing",0),lambda:r.set("start",0),lambda:r.if_(("negative","start"),lambda:r.set("start",add("start","length"))))
        r.if_(("ne","stop_missing",0),lambda:r.set("stop","length"),lambda:r.if_(("negative","stop"),lambda:r.set("stop",add("stop","length"))))
        for name in ("start","stop"):
            r.if_(("slt",name,0),lambda name=name:r.set(name,0))
            r.if_(("slt","length",name),lambda name=name:r.set(name,"length"))
        r.set("magnitude","step")
    def negative():
        r.if_(("ne","start_missing",0),lambda:r.set("start",sub("length",1)),lambda:r.if_(("negative","start"),lambda:r.set("start",add("start","length"))))
        r.if_(("ne","stop_missing",0),lambda:r.set("stop",-1),lambda:r.if_(("negative","stop"),lambda:r.set("stop",add("stop","length"))))
        for name in ("start","stop"):
            r.if_(("slt",name,-1),lambda name=name:r.set(name,-1))
            r.if_(("sge",name,"length"),lambda name=name:r.set(name,sub("length",1)))
        r.set("magnitude",sub(0,"step"))
    r.if_(("negative","step"),negative,positive)
    def byte_slice():
        r.set("count",0)
        r.if_(("slt","start","stop"),lambda:r.set("count",sub("stop","start")))
        r.ret(call("from_data",1,"count",add(word("object"),"start")))
    r.if_(("eq",word(add("object",6)),1),lambda:r.if_(("eq","step",1),byte_slice))
    def indices(action):
        r.set("current","start")
        begin,end=r.label(),r.label();r.mark(begin)
        r.if_(("negative","step"),lambda:r.false_jump(("slt","stop","current"),end),lambda:r.false_jump(("slt","current","stop"),end))
        action()
        r.if_(("negative","step"),lambda:r.set("value",sub("current","stop")),lambda:r.set("value",sub("stop","current")))
        r.false_jump(("ult","magnitude","value"),end)
        r.set("current",add("current","step"));r.emit(f"jp {begin}");r.mark(end)
    r.set("count",0)
    indices(lambda:r.set("count",add("count",1)))
    def tuple_result():
        r.set("flags",call("malloc","count"));r.set("index",0)
        def flag():
            r.write(add("flags","index"),byte(add(word(add("object",10)),"current")),1)
            r.set("index",add("index",1))
        indices(flag)
        r.set("result",call("tuple_new","flags","count"));r.invoke("free","flags");r.set("index",0)
        def copy():
            r.invoke("tuple_set","result","index",("call","__obj_read","object","current"))
            r.set("index",add("index",1))
        indices(copy)
    def array_result():
        r.set("result",call("new",word(add("object",6))));r.invoke("reserve","result","count")
        indices(lambda:r.invoke("append","result",("call","__obj_read","object","current")))
    r.if_(("eq",word(add("object",6)),32766),tuple_result,array_result)
    r.ret("result");out.append(r.finish())

    r=Routine("concat",("left","right"),("result","length","added","flags","i"))
    r.set("length",word(add("left",2)));r.set("added",word(add("right",2)))
    def tuple_concat():
        r.set("flags",call("malloc",add("length","added")))
        r.invoke("copy_bytes","flags",word(add("left",10)),"length")
        r.invoke("copy_bytes",add("flags","length"),word(add("right",10)),"added")
        r.set("result",call("tuple_new","flags",add("length","added")));r.invoke("free","flags")
        r.set("i",0)
        def first():
            r.invoke("tuple_set","result","i",("call","__obj_read","left","i"));r.set("i",add("i",1))
        r.while_(("ult","i","length"),first);r.set("i",0)
        def second():
            r.invoke("tuple_set","result",add("length","i"),("call","__obj_read","right","i"));r.set("i",add("i",1))
        r.while_(("ult","i","added"),second)
    def array_concat():
        r.set("result",call("new",word(add("left",6))))
        r.invoke("reserve","result",add("length","added"));r.invoke("extend","result","left");r.invoke("extend","result","right")
    r.if_(("eq",word(add("left",6)),32766),tuple_concat,array_concat)
    r.ret("result");out.append(r.finish())

    r=Routine("repeat",("object","count"),("result","i","flags","length","size","index","source_index"))
    r.if_(("negative","count"),lambda:r.set("count",0))
    r.set("length",word(add("object",2)));r.set("size",0);r.set("i",0)
    r.if_(("eq","length",0),lambda:r.set("count",0))
    def size():
        r.set("size",add("size","length"));r.if_(("uge","size",4097),lambda:r.fail(3));r.set("i",add("i",1))
    r.while_(("ult","i","count"),size)
    def tuple_repeat():
        r.set("flags",call("malloc","size"));r.set("i",0);r.set("index",0)
        def flags():
            r.invoke("copy_bytes",add("flags","index"),word(add("object",10)),"length")
            r.set("index",add("index","length"));r.set("i",add("i",1))
        r.while_(("ult","i","count"),flags)
        r.set("result",call("tuple_new","flags","size"));r.invoke("free","flags")
        r.set("index",0);r.set("source_index",0)
        def element():
            r.invoke("tuple_set","result","index",("call","__obj_read","object","source_index"))
            r.set("index",add("index",1));r.set("source_index",add("source_index",1))
            r.if_(("eq","source_index","length"),lambda:r.set("source_index",0))
        r.while_(("ult","index","size"),element)
    def array_repeat():
        r.set("result",call("new",word(add("object",6))));r.invoke("reserve","result","size");r.set("i",0)
        def repeat():r.invoke("extend","result","object");r.set("i",add("i",1))
        r.while_(("ult","i","count"),repeat)
    r.if_(("eq",word(add("object",6)),32766),tuple_repeat,array_repeat)
    r.ret("result");out.append(r.finish())
    r=Routine("repeat_reverse",("count","object"));r.ret(call("repeat","object","count"));out.append(r.finish())

    r=Routine("sort",("object","reverse"),("i","j","key","previous","comparison","pointer","width","move"))
    r.set("width",call("bytes","object",1));r.set("i",1)
    def insert():
        r.set("key",("call","__obj_read","object","i"));r.set("j","i")
        begin,end=r.label(),r.label();r.mark(begin)
        r.false_jump(("ne","j",0),end)
        r.set("previous",("call","__obj_read","object",sub("j",1)))
        def managed_compare():r.set("comparison",("call","__obj_compare","previous","key"))
        def scalar_compare():
            r.set("comparison",0)
            r.if_(("slt","previous","key"),lambda:r.set("comparison",-1))
            r.if_(("slt","key","previous"),lambda:r.set("comparison",1))
        r.if_(("eq",word(add("object",6)),32765),managed_compare,scalar_compare)
        r.set("move",0)
        r.if_(("ne","reverse",0),lambda:r.if_(("eq","comparison",-1),lambda:r.set("move",1)),lambda:r.if_(("eq","comparison",1),lambda:r.set("move",1)))
        r.false_jump(("ne","move",0),end)
        r.set("pointer",add(word("object"),call("bytes","object","j")))
        r.if_(("eq","width",1),lambda:r.write("pointer","previous",1),lambda:r.write("pointer","previous"))
        r.set("j",sub("j",1));r.emit(f"jp {begin}");r.mark(end)
        r.set("pointer",add(word("object"),call("bytes","object","j")))
        r.if_(("eq","width",1),lambda:r.write("pointer","key",1),lambda:r.write("pointer","key"))
        r.set("i",add("i",1))
    r.while_(("ult","i",word(add("object",2))),insert)
    r.ret();out.append(r.finish())
    r=Routine("sorted",("object","reverse"),("result",))
    r.set("result",call("copy","object"));r.invoke("sort","result","reverse");r.ret("result");out.append(r.finish())
    return out
