"""String-specific native loops; managed lists keep their generic operations."""
from .list_runtime import add, sub, word, byte, call


def routines():
    # Import lazily: string_runtime installs these replacements after defining
    # its common authoring helpers. All resulting code executes on the Z80.
    from .string_runtime import StringRoutine, sc
    out = []

    for name in ("upper", "lower", "capitalize", "title", "swapcase"):
        r = StringRoutine(name, ("object",), ("result", "length", "previous"))
        r.set("length", word(add("object", 2)))
        r.set("result", call("new", 1))
        r.invoke("reserve", "result", "length")
        if name in {"capitalize", "title"}:
            r.set("previous", 0)
        done, loop, unchanged = r.label(), r.label(), r.label()
        r.false_jump(("ne", "length", 0), done)
        r.expr(word("object")); r.emit("push hl")
        r.expr(word("result")); r.emit("push hl")
        r.expr("length"); r.emit("ld b,h", "ld c,l", "pop de", "pop hl")
        r.mark(loop)
        r.emit("ld a,(hl)")

        def convert(upper):
            low, high = (97, 123) if upper else (65, 91)
            r.emit(f"cp {low}", f"jr c,{unchanged}", f"cp {high}",
                   f"jr nc,{unchanged}", "sub 32" if upper else "add a,32")

        if name in {"upper", "lower"}:
            convert(name == "upper")
        elif name in {"capitalize", "title"}:
            lower = r.label()
            r.emit("push af", f"ld a,(ix{r.slots['previous']:+d})", "or a", f"jr nz,{lower}", "pop af")
            convert(True); r.emit(f"jr {unchanged}")
            r.mark(lower); r.emit("pop af"); convert(False)
        else:
            uppercase = r.label()
            r.emit("cp 65", f"jr c,{unchanged}", "cp 91", f"jr c,{uppercase}",
                   "cp 97", f"jr c,{unchanged}", "cp 123", f"jr nc,{unchanged}", "sub 32", f"jr {unchanged}")
            r.mark(uppercase); r.emit("add a,32")
        r.mark(unchanged)
        r.emit("ld (de),a")
        if name == "capitalize":
            r.emit(f"ld (ix{r.slots['previous']:+d}),1")
        elif name == "title":
            nonletter, store = r.label(), r.label()
            r.emit("or 32", "cp 97", f"jr c,{nonletter}", "cp 123", f"jr nc,{nonletter}", "ld a,1", f"jr {store}")
            r.mark(nonletter); r.emit("xor a")
            r.mark(store); r.emit(f"ld (ix{r.slots['previous']:+d}),a")
        r.emit("inc hl", "inc de", "dec bc", "ld a,b", "or c", f"jr nz,{loop}")
        r.mark(done)
        r.write(add("result", 2), "length"); r.ret("result")
        out.append(r.finish())

    r = StringRoutine("find", ("object", "needle", "start", "stop"),
                      ("length", "size", "limit", "cursor", "candidates"))
    r.set("length", word(add("object", 2))); r.set("size", word(add("needle", 2)))
    for name in ("start", "stop"):
        r.if_(("negative", name), lambda name=name: r.set(name, add(name, "length")))
        r.if_(("slt", name, 0), lambda name=name: r.set(name, 0))
    r.if_(("slt", "length", "start"), lambda: r.ret(-1))
    r.if_(("slt", "length", "stop"), lambda: r.set("stop", "length"))
    r.if_(("slt", sub("stop", "start"), "size"), lambda: r.ret(-1))
    r.if_(("eq", "size", 0), lambda: r.ret("start"))
    r.set("limit", sub("stop", "size"))

    def search():
        r.set("cursor", add(word("object"), "start"))
        r.set("candidates", add(sub("limit", "start"), 1))
        r.expr(word("needle")); r.emit("ld a,(hl)", "push af")
        r.expr("candidates"); r.emit("ld b,h", "ld c,l")
        r.expr("cursor"); r.emit("pop af", "cpir")
        absent, mismatch, compare = r.label(), r.label(), r.label()
        r.emit(f"jp nz,{absent}"); r.store_hl("cursor")
        r.set("start", sub(sub("cursor", 1), word("object")))
        r.if_(("eq", "size", 1), lambda: r.ret("start"))
        r.expr(sub("cursor", 1)); r.emit("push hl")
        r.expr(word("needle")); r.emit("push hl")
        r.expr("size"); r.emit("ld b,h", "ld c,l", "pop de", "pop hl")
        r.mark(compare)
        r.emit("ld a,(de)", "cp (hl)", f"jr nz,{mismatch}", "inc de", "inc hl",
               "dec bc", "ld a,b", "or c", f"jr nz,{compare}")
        r.ret("start")
        r.mark(absent); r.ret(-1)
        r.mark(mismatch); r.set("start", add("start", 1))

    r.while_(("slt", "start", add("limit", 1)), search)
    r.ret(-1); out.append(r.finish())

    for name in ("startswith", "endswith"):
        r = StringRoutine(name, ("object", "needle", "start", "stop"), ("length", "size"))
        r.set("length", word(add("object", 2))); r.set("size", word(add("needle", 2)))
        for field in ("start", "stop"):
            r.if_(("negative", field), lambda field=field: r.set(field, add(field, "length")))
            r.if_(("slt", field, 0), lambda field=field: r.set(field, 0))
        r.if_(("slt", "length", "start"), lambda: r.ret(0))
        r.if_(("slt", "length", "stop"), lambda: r.set("stop", "length"))
        r.if_(("slt", sub("stop", "start"), "size"), lambda: r.ret(0))
        r.if_(("eq", "size", 0), lambda: r.ret(1))
        if name == "endswith":
            r.set("start", sub("stop", "size"))
        r.expr(word("needle")); r.emit("push hl")
        r.expr(add(word("object"), "start")); r.emit("push hl")
        r.expr("size"); r.emit("ld b,h", "ld c,l", "pop hl", "pop de")
        loop, mismatch = r.label(), r.label()
        r.mark(loop)
        r.emit("ld a,(de)", "cp (hl)", f"jr nz,{mismatch}", "inc de", "inc hl",
               "dec bc", "ld a,b", "or c", f"jr nz,{loop}")
        r.ret(1); r.mark(mismatch); r.ret(0)
        out.append(r.finish())

    r = StringRoutine("join", ("separator", "parts"), ("result", "i", "part", "length", "added", "cursor"))
    r.set("length", 0); r.set("i", 0)

    def measure():
        r.set("part", word(add(word("parts"), add("i", "i"))))
        r.set("added", word(add("part", 2)))
        r.if_(("ne", "i", 0), lambda: r.set("added", add("added", word(add("separator", 2)))))
        r.set("length", add("length", "added"))
        r.if_(("uge", "length", 4097), lambda: r.fail(3))
        r.set("i", add("i", 1))

    r.while_(("ult", "i", word(add("parts", 2))), measure)
    r.set("result", call("new", 1)); r.invoke("reserve", "result", "length")
    r.set("cursor", word("result")); r.set("i", 0)

    def copy(object_):
        r.set("added", word(add(object_, 2)))
        r.invoke("copy_bytes", "cursor", word(object_), "added")
        r.set("cursor", add("cursor", "added"))

    def join():
        r.if_(("ne", "i", 0), lambda: copy("separator"))
        r.set("part", word(add(word("parts"), add("i", "i"))))
        copy("part"); r.set("i", add("i", 1))

    r.while_(("ult", "i", word(add("parts", 2))), join)
    r.write(add("result", 2), "length"); r.ret("result"); out.append(r.finish())

    r = StringRoutine("concat", ("left", "right"), ("result", "length", "added"))
    r.set("length", word(add("left", 2))); r.set("added", word(add("right", 2)))
    r.set("result", call("new", 1)); r.invoke("reserve", "result", add("length", "added"))
    r.invoke("copy_bytes", word("result"), word("left"), "length")
    r.invoke("copy_bytes", add(word("result"), "length"), word("right"), "added")
    r.write(add("result", 2), add("length", "added")); r.ret("result"); out.append(r.finish())

    r = StringRoutine("concat_parts", ("parts", "count"), ("result", "length", "i", "added", "cursor"))
    r.set("length", 0); r.set("i", 0)

    def measure_part():
        r.set("added", word(add(add("parts", "i"), 2)))
        r.set("length", add("length", "added"))
        r.if_(("uge", "length", 4097), lambda: r.fail(3))
        r.set("i", add("i", 4))

    r.set("count", add(add("count", "count"), add("count", "count")))
    r.while_(("ult", "i", "count"), measure_part)
    r.set("result", call("new", 1)); r.invoke("reserve", "result", "length")
    r.set("cursor", word("result")); r.set("i", 0)

    def copy_part():
        r.set("added", word(add(add("parts", "i"), 2)))
        r.invoke("copy_bytes", "cursor", word(add("parts", "i")), "added")
        r.set("cursor", add("cursor", "added")); r.set("i", add("i", 4))

    r.while_(("ult", "i", "count"), copy_part)
    r.write(add("result", 2), "length"); r.ret("result"); out.append(r.finish())

    r = StringRoutine("repeat", ("object", "count"), ("result", "length", "size", "i", "copied", "chunk"))
    r.if_(("negative", "count"), lambda: r.set("count", 0))
    r.set("length", word(add("object", 2)))
    r.if_(("eq", "length", 0), lambda: r.set("count", 0))
    r.set("size", 0); r.set("i", 0)

    def measure_repeat():
        r.set("size", add("size", "length"))
        r.if_(("uge", "size", 4097), lambda: r.fail(3))
        r.set("i", add("i", 1))

    r.while_(("ult", "i", "count"), measure_repeat)
    r.set("result", call("new", 1)); r.invoke("reserve", "result", "size")

    def fill():
        r.invoke("copy_bytes", word("result"), word("object"), "length")
        r.set("copied", "length")

        def double():
            r.set("chunk", sub("size", "copied"))
            r.if_(("ult", "copied", "chunk"), lambda: r.set("chunk", "copied"))
            r.invoke("copy_bytes", add(word("result"), "copied"), word("result"), "chunk")
            r.set("copied", add("copied", "chunk"))

        r.while_(("ult", "copied", "size"), double)

    r.if_(("ne", "size", 0), fill)
    r.write(add("result", 2), "size"); r.ret("result"); out.append(r.finish())
    r = StringRoutine("repeat_reverse", ("count", "object"))
    r.ret(sc("repeat", "object", "count")); out.append(r.finish())
    return out
