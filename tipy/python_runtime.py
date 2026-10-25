"""Python-like builtins lowered to ordinary Z80; linked by dependency only."""

from .list_runtime import Routine, add, sub, word, call


def builtin_routines():
    routines = []
    r = Routine("from_data", ("width", "count", "source"), ("result",))
    r.set("result", call("new", "width"))
    r.invoke("reserve", "result", "count")
    r.invoke("copy_bytes", word("result"), "source", call("bytes", "result", "count"))
    r.write(add("result", 2), "count")
    r.ret("result"); routines.append(r.finish())
    r = Routine("contains", ("value", "object"), ("i", "length"))
    r.set("i", 0); r.set("length", word(add("object", 2)))
    def check_member():
        r.if_(("eq", call("get", "object", "i"), "value"), lambda: r.ret(1))
        r.set("i", add("i", 1))
    r.while_(("ult", "i", "length"), check_member)
    r.ret(0); routines.append(r.finish())
    r = Routine("range", ("start", "stop", "step"), ("result", "current", "magnitude"))
    r.if_(("eq", "step", 0), lambda: r.fail(7))
    r.set("result", call("new", 2)); r.set("current", "start")
    def descending():
        r.set("magnitude", sub(0, "step"))
        def advance():
            r.invoke("append", "result", "current")
            r.if_(("uge", "magnitude", sub("current", "stop")), lambda: r.ret("result"))
            r.set("current", add("current", "step"))
        r.while_(("slt", "stop", "current"), advance)
        r.ret("result")
    r.if_(("negative", "step"), descending)
    def advance():
        r.invoke("append", "result", "current")
        r.if_(("uge", "step", sub("stop", "current")), lambda: r.ret("result"))
        r.set("current", add("current", "step"))
    r.while_(("slt", "current", "stop"), advance)
    r.ret("result"); routines.append(r.finish())

    for name in ["sum", "min", "max", "any", "all"]:
        r = Routine(name, ("object", "start") if name == "sum" else ("object",), ("i", "length", "result", "value"))
        r.set("length", word(add("object", 2)))
        r.set("i", 0)
        r.set("result", "start" if name == "sum" else 1 if name == "all" else 0)
        if name in {"min", "max"}:
            r.if_(("eq", "length", 0), lambda: r.fail(6))
            r.set("result", call("get", "object", 0)); r.set("i", 1)
        def body(name=name, r=r):
            r.set("value", call("get", "object", "i"))
            if name == "sum":
                r.set("result", add("result", "value"))
            elif name in {"min", "max"}:
                a, b = ("value", "result") if name == "min" else ("result", "value")
                r.if_(("slt", a, b), lambda: r.set("result", "value"))
            else:
                r.if_(("ne" if name == "any" else "eq", "value", 0), lambda: r.ret(1 if name == "any" else 0))
            r.set("i", add("i", 1))
        r.while_(("ult", "i", "length"), body)
        r.ret("result"); routines.append(r.finish())
    return routines
