"""A starred target receives a fresh, homogeneous native list."""
from .list_runtime import Routine,add,sub,word,call


def routines():
    r=Routine("unpack_rest",("object","start","tail","width","strings"),("result","index","stop","value"),prefix="__obj_")
    r.set("result",call("new","width"));r.set("index","start");r.set("stop",sub(word(add("object",2)),"tail"))
    def element():
        r.if_(("ne","strings",0),lambda:r.set("value",("call","__str_get","object","index")),lambda:r.set("value",("call","__obj_read","object","index")))
        r.invoke("append","result","value")
        r.if_(("ne","strings",0),lambda:r.invoke("release","value"));r.set("index",add("index",1))
    r.while_(("ult","index","stop"),element);r.ret("result");return [r.finish()]
