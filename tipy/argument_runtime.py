"""Native typed argument binding after source-order argument snapshots."""
from .list_runtime import Routine,add,word,call
from .dict_runtime import dc
from .types import managed,tuple_types


def binder(item,label):
    names=item["names"];t=item["element_type"];types=tuple_types(item["type"])
    r=Routine(label,("positional","keywords","defaults"),("result","found","slot","value","slice","varargs"),prefix="")
    if not item["variadic"]:r.if_(("uge",word(add("positional",2)),len(names)+1),lambda:r.fail(17))
    r.set("result",call("tuple_new","@"+label+"_flags",len(types)));r.set("found",0)
    for i,name in enumerate(names):
        if item["keywords_used"]:
            r.set("slot",dc("find","keywords","@"+label+f"_name_{i}"))
            r.if_(("ne","slot",0),lambda:r.set("found",add("found",1)))
        else:r.set("slot",0)
        def positional(i=i):
            r.if_(("ne","slot",0),lambda:r.fail(17));r.set("value",("call","__obj_read","positional",i))
        def keyword(i=i):
            def missing():
                if item["defaults"][i]:r.set("value",("call","__obj_read","defaults",i))
                else:r.fail(17)
            r.if_(("ne","slot",0),lambda:r.set("value",word(add("slot",6))),missing)
        r.if_(("uge",word(add("positional",2)),i+1),positional,keyword)
        r.invoke("tuple_set","result",i,"value")
    if item["keywords_used"]:r.if_(("ne","found",word(add("keywords",2))),lambda:r.fail(17))
    if item["variadic"]:
        r.set("slice",call("slice","positional",len(names),0,1,0,1))
        r.set("varargs",call("to_tuple","slice",int(managed(t))));r.invoke("release","slice")
        r.invoke("tuple_set","result",len(names),"varargs");r.invoke("release","varargs")
    r.ret("result");result=r.finish()+"\n"+label+"_flags:\n        db "+(",".join(str(int(managed(t))) for t in types) or "0")
    for i,name in enumerate(names if item["keywords_used"] else []):
        result+=f"\n{label}_name_{i}:\n        dw {label}_bytes_{i},{len(name)},{len(name)},1,1\n{label}_bytes_{i}:\n        db "+(",".join(str(ord(c)) for c in name) or "0")
    return result
