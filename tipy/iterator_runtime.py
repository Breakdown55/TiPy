"""Demand-generated, typed native iterator adapters. map/filter stay lazy."""
from .list_runtime import Routine,add,word,call
from .types import iterator_types,managed,is_dict,dict_types


def new_routine():
    r=Routine("iterator_new",("source","reverse","callback","mapping"),("object","index","version","entry"),prefix="__obj_")
    r.set("index",0);r.set("version",0);r.set("entry",0)
    r.if_(("ne","reverse",0),lambda:r.set("index",("-",word(add("source",2)),1)))
    def mapping():
        r.set("version",word(add("source",18)));r.set("entry",word(add("source",14)))
    r.if_(("ne","mapping",0),mapping)
    r.set("object",call("tuple_new","@__iterator_flags",6))
    for i,value in enumerate(("source","index","reverse","callback","version","entry")):r.invoke("tuple_set","object",i,value)
    r.ret("object")
    return r.finish()+"\n__iterator_flags:\n        db 1,0,0,1,0,0"


def next_routine(type_name,label,ensure,functions):
    result,mode,*parts=iterator_types(type_name)
    source_types=parts[1:] if mode in {"map","filter"} else []
    locals_=("source","index","value","callback","predicate",*[f"arg{i}" for i in range(len(source_types))])
    r=Routine(label,("object",),locals_,prefix="")
    def field(i):return ("call","__obj_read","object",i)
    def success(value):r.write("@__iterator_exhausted",0);r.ret(value)
    def exhausted():r.write("@__iterator_exhausted",1);r.ret(0)
    if mode=="generator":
        r.ret(("call","__obj_gen_resume","object"))
    elif mode in {"sequence","string"}:
        r.set("source",field(0));r.set("index",field(1))
        r.if_(("uge","index",word(add("source",2))),exhausted)
        if mode=="string":r.set("value",("call","__str_get","source","index"))
        else:
            r.set("value",("call","__obj_read","source","index"))
            if managed(result):r.invoke("retain","value")
        r.if_(("ne",field(2),0),lambda:r.set("index",("-","index",1)),lambda:r.set("index",add("index",1)))
        r.write(add(word("object"),2),"index");success("value")
    elif mode in {"keys","values","items"}:
        r.set("source",field(0))
        r.if_(("ne",field(4),word(add("source",18))),lambda:r.fail(13))
        r.set("index",field(5));r.if_(("eq","index",0),exhausted)
        r.write(add(word("object"),10),word(add("index",8)))
        if mode=="items":
            key,val=dict_types(parts[0]);flags=(int(managed(key)),int(managed(val)))
            data=label+"_flags";r.set("value",call("tuple_new","@"+data,2))
            for i,offset in enumerate((4,6)):r.invoke("tuple_set","value",i,word(add("index",offset)))
            success("value");return r.finish()+"\n"+data+":\n        db "+",".join(map(str,flags))
        r.set("value",word(add("index",6 if mode=="values" else 4)))
        if managed(result):r.invoke("retain","value")
        success("value")
    elif mode in {"map","filter"}:
        r.set("source",field(0));r.set("callback",field(3))
        loop=r.label();r.mark(loop)
        for i,t in enumerate(source_types):
            r.set(f"arg{i}",("call",ensure(t),("call","__obj_read","source",i)))
            def stop(i=i):
                for j in range(i):
                    if managed(iterator_types(source_types[j])[0]):r.expr(("call","__obj_release",f"arg{j}"))
                exhausted()
            r.if_(("ne",word("@__iterator_exhausted"),0),stop)
        function=functions[parts[0][8:-1]];args=[]
        if function.get("closure_env"):
            args.append(call("retain",("call","__obj_read","callback",1)))
        for i,t in enumerate(source_types):
            arg=f"arg{i}"
            if mode=="filter" and managed(iterator_types(t)[0]):arg=call("retain",arg)
            args.append(arg)
        r.set("value",("call",function["label"],*args))
        if mode=="map":success("value")
        else:
            if managed(function["return_type"]):
                r.set("predicate",("call","__obj_truth","value"));r.expr(("call","__obj_release","value"))
            else:r.set("predicate","value")
            r.if_(("ne","predicate",0),lambda:success("arg0"))
            if managed(result):r.expr(("call","__obj_release","arg0"))
            r.emit(f"jp {loop}")
    return r.finish()


def list_routine(type_name,label,next_label):
    from .types import width
    t=iterator_types(type_name)[0];r=Routine(label,("object",),("result","value"),prefix="")
    r.set("result",call("new",width(t+"[]")));loop=r.label();r.mark(loop)
    r.set("value",("call",next_label,"object"))
    r.if_(("ne",word("@__iterator_exhausted"),0),lambda:r.ret("result"))
    r.invoke("append","result","value")
    if managed(t):r.expr(("call","__obj_release","value"))
    r.emit(f"jp {loop}");return r.finish()
