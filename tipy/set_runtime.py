"""Native sets share the ordered hash-table storage, with unused zero values."""
from .list_runtime import Routine,add,word,call
from .dict_runtime import dc


def st(name,*args):return ("call","__set_"+name,*args)


def routines():
    out=[]
    r=Routine("from_sequence",("object","strings"),("result","index"),prefix="__set_")
    r.set("result",dc("new","strings",0,0));r.set("index",0)
    def add_element():
        r.expr(dc("set","result",("call","__obj_read","object","index"),0));r.set("index",add("index",1))
    r.while_(("ult","index",word(add("object",2))),add_element)
    r.ret("result");out.append(r.finish())
    r=Routine("add",("object","value"),prefix="__set_");r.ret(dc("set","object","value",0));out.append(r.finish())
    r=Routine("remove",("object","value"),prefix="__set_");r.ret(dc("delete","object","value"));out.append(r.finish())
    r=Routine("discard",("object","value"),("slot",),prefix="__set_")
    r.set("slot",dc("find","object","value"));r.if_(("ne","slot",0),lambda:r.expr(dc("remove","object","slot",1)))
    r.ret();out.append(r.finish())
    r=Routine("pop",("object",),("entry","value"),prefix="__set_")
    r.set("entry",word(add("object",14)));r.if_(("eq","entry",0),lambda:r.fail(12))
    r.set("value",word(add("entry",4)))
    r.if_(("ne",word(add("object",10)),0),lambda:r.set("value",call("retain","value")))
    r.expr(dc("remove","object","entry",1));r.ret("value");out.append(r.finish())

    for name in ("union","intersection","difference","symmetric_difference"):
        r=Routine(name,("left","right"),("result","entry","key","found"),prefix="__set_")
        if name=="union":
            r.set("result",dc("copy","left"));r.expr(dc("update","result","right"))
        else:
            r.set("result",dc("new",word(add("left",10)),0,0));r.set("entry",word(add("left",14)))
            def visit():
                r.set("key",word(add("entry",4)));r.set("found",dc("contains","key","right"))
                r.if_(("ne" if name=="intersection" else "eq","found",0),lambda:r.expr(dc("set","result","key",0)))
                r.set("entry",word(add("entry",8)))
            r.while_(("ne","entry",0),visit)
            if name=="symmetric_difference":
                r.set("entry",word(add("right",14)))
                def other():
                    r.set("key",word(add("entry",4)));r.if_(("eq",dc("contains","key","left"),0),lambda:r.expr(dc("set","result","key",0)))
                    r.set("entry",word(add("entry",8)))
                r.while_(("ne","entry",0),other)
        r.ret("result");out.append(r.finish())

    for name in ("update","intersection_update","difference_update","symmetric_difference_update"):
        r=Routine(name,("object","other"),("entry","next","key","found"),prefix="__set_")
        if name=="update":r.expr(dc("update","object","other"))
        else:
            source="other" if name=="symmetric_difference_update" else "object"
            r.set("entry",word(add(source,14)))
            def visit():
                r.set("next",word(add("entry",8)));r.set("key",word(add("entry",4)))
                r.set("found",dc("contains","key","object" if name=="symmetric_difference_update" else "other"))
                def remove():r.expr(st("discard","object","key"))
                if name=="symmetric_difference_update":r.if_(("ne","found",0),remove,lambda:r.expr(dc("set","object","key",0)))
                else:r.if_(("eq" if name=="intersection_update" else "ne","found",0),remove)
                r.set("entry","next")
            r.while_(("ne","entry",0),visit)
        r.ret();out.append(r.finish())

    for name in ("issubset","issuperset","isdisjoint"):
        r=Routine(name,("left","right","strict"),("entry","found"),prefix="__set_")
        if name=="issuperset":r.ret(st("issubset","right","left","strict"))
        else:
            if name=="issubset":
                r.if_(("ne","strict",0),lambda:r.if_(("uge",word(add("left",2)),word(add("right",2))),lambda:r.ret(0)))
            r.set("entry",word(add("left",14)))
            def visit():
                r.set("found",dc("contains",word(add("entry",4)),"right"))
                r.if_(("ne" if name=="isdisjoint" else "eq","found",0),lambda:r.ret(0))
                r.set("entry",word(add("entry",8)))
            r.while_(("ne","entry",0),visit);r.ret(1)
        out.append(r.finish())
    return out
