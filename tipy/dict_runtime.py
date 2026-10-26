"""Ordered open-addressed hash tables, linked only by native call dependencies.

Descriptor words: buckets, length, capacity, marker, refs, string-key flag,
string-value flag, first entry, last entry, structural version (20 bytes).
Buckets contain state/hash/key/value/next/previous words (12 bytes). Deleted
buckets are tombstones. String keys/values own references to immutable strings.
"""

from .list_runtime import Routine, add, sub, word, byte, call


def dc(name, *args): return ("call", "__dict_" + name, *args)


class MapRoutine(Routine):
    key_strings = True
    value_strings = True
    def __init__(self, name, params=(), locals_=()):
        super().__init__(name, params, locals_, prefix="__dict_")

    def invoke_dict(self, name, *args): self.expr(dc(name, *args))

    def if_(self, condition, yes, no=None):
        operation, *values = condition
        if operation in {"eq", "ne"} and len(values) == 2 and values[1] == 0:
            for offset, possible in ((10, self.key_strings), (12, self.value_strings)):
                if not possible and values[0] == word(add("object", offset)):
                    if operation == "eq": yes()
                    elif no: no()
                    return
        super().if_(condition, yes, no)

    def store_hl(self, name):
        offset = self.slots[name]
        self.emit(f"ld (ix{offset:+d}),l", f"ld (ix{offset+1:+d}),h")

    def address(self):
        self.expr("index")
        self.emit("ld d,h", "ld e,l", "add hl,hl", "add hl,de", "add hl,hl", "add hl,hl", "push hl")
        self.expr(word("object")); self.emit("pop de", "add hl,de")
        self.store_hl("slot")

    def mask_hash(self):
        self.expr("hash"); self.emit("push hl")
        self.expr(sub(word(add("object", 4)), 1))
        self.emit("ex de,hl", "pop hl", "ld a,h", "and d", "ld h,a", "ld a,l", "and e", "ld l,a")
        self.store_hl("index")

    def next_probe(self):
        self.set("index", add("index", 1))
        self.if_(("eq", "index", word(add("object", 4))), lambda: self.set("index", 0))

    def retain_value(self, name="value"):
        self.if_(("ne", word(add("object", 12)), 0), lambda: self.set(name, call("retain", name)))


def routines(key_strings=True, value_strings=True):
    class SpecializedMap(MapRoutine): pass
    SpecializedMap.key_strings = key_strings
    SpecializedMap.value_strings = value_strings
    result = []
    r = SpecializedMap("hash_str", ("key",), ("data", "length", "index", "hash"))
    r.set("data", word("key")); r.set("length", word(add("key", 2)))
    r.set("hash", 5381); r.set("index", 0)
    def hash_byte():
        r.expr("hash"); r.emit("ld d,h", "ld e,l", *( ["add hl,hl"] * 5 ), "add hl,de")
        r.store_hl("hash")
        r.expr(byte(add("data", "index"))); r.emit("ld a,l")
        r.expr("hash"); r.emit("xor l", "ld l,a"); r.store_hl("hash")
        r.set("index", add("index", 1))
    r.while_(("ult", "index", "length"), hash_byte)
    r.ret("hash"); result.append(r.finish())

    r = SpecializedMap("equal_str", ("a", "b"), ("length", "index", "left", "right"))
    r.if_(("eq", "a", "b"), lambda: r.ret(1))
    r.set("length", word(add("a", 2)))
    r.if_(("ne", "length", word(add("b", 2))), lambda: r.ret(0))
    r.set("left", word("a")); r.set("right", word("b")); r.set("index", 0)
    def equal_byte():
        r.if_(("ne", byte(add("left", "index")), byte(add("right", "index"))), lambda: r.ret(0))
        r.set("index", add("index", 1))
    r.while_(("ult", "index", "length"), equal_byte)
    r.ret(1); result.append(r.finish())

    r = SpecializedMap("hash", ("object", "key"))
    r.if_(("ne", word(add("object", 10)), 0), lambda: r.ret(dc("hash_str", "key")))
    r.ret("key"); result.append(r.finish())

    r = SpecializedMap("find", ("object", "key"), ("hash", "index", "slot", "probes"))
    r.if_(("eq", word(add("object", 4)), 0), lambda: r.ret(0))
    r.set("hash", dc("hash", "object", "key")); r.mask_hash(); r.set("probes", 0)
    def probe():
        r.address()
        r.if_(("eq", word("slot"), 0), lambda: r.ret(0))
        def live():
            def same_hash():
                r.if_(("ne", word(add("object", 10)), 0),
                      lambda: r.if_(("ne", dc("equal_str", word(add("slot", 4)), "key"), 0), lambda: r.ret("slot")),
                      lambda: r.if_(("eq", word(add("slot", 4)), "key"), lambda: r.ret("slot")))
            r.if_(("eq", word(add("slot", 2)), "hash"), same_hash)
        r.if_(("eq", word("slot"), 1), live)
        r.next_probe(); r.set("probes", add("probes", 1))
    r.while_(("ult", "probes", word(add("object", 4))), probe)
    r.ret(0); result.append(r.finish())

    r = SpecializedMap("place", ("object", "hash"), ("index", "slot", "probes"))
    r.mask_hash(); r.set("probes", 0)
    def place():
        r.address()
        r.if_(("ne", word("slot"), 1), lambda: r.ret("slot"))
        r.next_probe(); r.set("probes", add("probes", 1))
    r.while_(("ult", "probes", word(add("object", 4))), place)
    r.fail(3); result.append(r.finish())

    r = SpecializedMap("link", ("object", "slot", "key", "value", "hash"), ("last",))
    r.set("last", word(add("object", 16)))
    for offset, value in ((0,1),(2,"hash"),(4,"key"),(6,"value"),(8,0),(10,"last")):
        r.write(add("slot", offset), value)
    r.if_(("eq", "last", 0), lambda: r.write(add("object", 14), "slot"), lambda: r.write(add("last", 8), "slot"))
    r.write(add("object", 16), "slot")
    r.ret(); result.append(r.finish())

    r = SpecializedMap("rehash", ("object", "capacity"), ("old", "entry", "next", "data", "size", "slot"))
    r.if_(("uge", "capacity", 1025), lambda: r.fail(3))
    r.set("old", word("object")); r.set("entry", word(add("object", 14)))
    r.expr("capacity"); r.emit("ld d,h", "ld e,l", "add hl,hl", "add hl,de", "add hl,hl", "add hl,hl")
    r.store_hl("size"); r.set("data", call("malloc", "size"))
    r.expr("data"); r.emit("ld (hl),0", "ld d,h", "ld e,l", "inc de", "push hl", "push de")
    r.expr(sub("size", 1)); r.emit("ld b,h", "ld c,l", "pop de", "pop hl", "ldir")
    r.write("object", "data"); r.write(add("object", 4), "capacity")
    r.write(add("object", 14), 0); r.write(add("object", 16), 0)
    def relocate():
        r.set("next", word(add("entry", 8)))
        r.set("slot", dc("place", "object", word(add("entry", 2))))
        r.invoke_dict("link", "object", "slot", word(add("entry", 4)), word(add("entry", 6)), word(add("entry", 2)))
        r.set("entry", "next")
    r.while_(("ne", "entry", 0), relocate)
    r.invoke("free", "old"); r.ret(); result.append(r.finish())

    r = SpecializedMap("new", ("key_string", "value_string", "initial"), ("object", "capacity"))
    r.if_(("uge", "initial", 513), lambda: r.fail(3))
    r.set("object", call("malloc", 20))
    for offset in (0,2,4,14,16,18): r.write(add("object", offset), 0)
    r.write(add("object", 6), 32767); r.write(add("object", 8), 1)
    r.write(add("object", 10), "key_string"); r.write(add("object", 12), "value_string")
    def reserve():
        r.set("capacity", 8)
        r.while_(("ult", "capacity", add("initial", "initial")), lambda: r.set("capacity", add("capacity", "capacity")))
        r.invoke_dict("rehash", "object", "capacity")
    r.if_(("ne", "initial", 0), reserve)
    r.ret("object"); result.append(r.finish())

    r = SpecializedMap("set", ("object", "key", "value"), ("slot", "capacity", "hash"))
    r.set("slot", dc("find", "object", "key"))
    def replace():
        r.retain_value()
        r.if_(("ne", word(add("object", 12)), 0), lambda: r.invoke("release", word(add("slot", 6))))
        r.write(add("slot", 6), "value"); r.ret()
    r.if_(("ne", "slot", 0), replace)
    r.set("capacity", word(add("object", 4)))
    r.expr("capacity"); r.emit("srl h", "rr l"); r.store_hl("hash")
    def grow():
        r.if_(("eq", "capacity", 0), lambda: r.set("capacity", 4))
        r.invoke_dict("rehash", "object", add("capacity", "capacity"))
    r.if_(("ult", "hash", add(word(add("object", 2)), 1)), grow)
    r.set("hash", dc("hash", "object", "key")); r.set("slot", dc("place", "object", "hash"))
    r.if_(("ne", word(add("object", 10)), 0), lambda: r.set("key", call("retain", "key")))
    r.retain_value()
    r.invoke_dict("link", "object", "slot", "key", "value", "hash")
    r.write(add("object", 2), add(word(add("object", 2)), 1))
    r.write(add("object", 18), add(word(add("object", 18)), 1))
    r.ret(); result.append(r.finish())

    for name in ("get", "get_default", "contains", "setdefault"):
        params = ("key", "object") if name == "contains" else ("object", "key", "default") if name in {"get_default", "setdefault"} else ("object", "key")
        r = SpecializedMap(name, params, ("slot", "value"))
        r.set("slot", dc("find", "object", "key"))
        if name == "contains":
            r.if_(("ne", "slot", 0), lambda: r.ret(1)); r.ret(0)
        else:
            def missing():
                if name == "get": r.fail(12)
                elif name == "setdefault":
                    r.invoke_dict("set", "object", "key", "default")
                    r.ret(dc("get", "object", "key"))
                else:
                    r.set("value", "default"); r.retain_value(); r.ret("value")
            r.if_(("eq", "slot", 0), missing)
            r.set("value", word(add("slot", 6))); r.retain_value(); r.ret("value")
        result.append(r.finish())

    r = SpecializedMap("remove", ("object", "slot", "drop_value"), ("previous", "next", "value"))
    r.set("previous", word(add("slot", 10))); r.set("next", word(add("slot", 8)))
    r.set("value", word(add("slot", 6)))
    r.if_(("eq", "previous", 0), lambda: r.write(add("object", 14), "next"), lambda: r.write(add("previous", 8), "next"))
    r.if_(("eq", "next", 0), lambda: r.write(add("object", 16), "previous"), lambda: r.write(add("next", 10), "previous"))
    r.if_(("ne", word(add("object", 10)), 0), lambda: r.invoke("release", word(add("slot", 4))))
    r.if_(("ne", "drop_value", 0), lambda: r.if_(("ne", word(add("object", 12)), 0), lambda: r.invoke("release", "value")))
    r.write("slot", 2)
    r.write(add("object", 2), sub(word(add("object", 2)), 1))
    r.write(add("object", 18), add(word(add("object", 18)), 1))
    r.ret("value"); result.append(r.finish())

    for name in ("delete", "pop", "pop_default"):
        r = SpecializedMap(name, ("object", "key", "default") if name == "pop_default" else ("object", "key"), ("slot", "value"))
        r.set("slot", dc("find", "object", "key"))
        def missing():
            if name != "pop_default": r.fail(12)
            else:
                r.set("value", "default"); r.retain_value(); r.ret("value")
        r.if_(("eq", "slot", 0), missing)
        r.ret(dc("remove", "object", "slot", int(name == "delete")))
        result.append(r.finish())

    r = SpecializedMap("clear", ("object",), ("entry", "next"))
    r.set("entry", word(add("object", 14)))
    r.if_(("ne", "entry", 0), lambda: r.write(add("object", 18), add(word(add("object", 18)), 1)))
    def destroy_entry():
        r.set("next", word(add("entry", 8)))
        r.if_(("ne", word(add("object", 10)), 0), lambda: r.invoke("release", word(add("entry", 4))))
        r.if_(("ne", word(add("object", 12)), 0), lambda: r.invoke("release", word(add("entry", 6))))
        r.set("entry", "next")
    r.while_(("ne", "entry", 0), destroy_entry)
    r.invoke("free", word("object"))
    for offset in (0,2,4,14,16): r.write(add("object", offset), 0)
    r.ret(); result.append(r.finish())

    r = SpecializedMap("release", ("object",), ("refs",))
    r.if_(("eq", "object", 0), lambda: r.ret())
    r.set("refs", sub(word(add("object", 8)), 1)); r.write(add("object", 8), "refs")
    def destroy():
        r.invoke_dict("clear", "object"); r.invoke("free", "object")
    r.if_(("eq", "refs", 0), destroy)
    r.ret(); result.append(r.finish())

    r = SpecializedMap("update", ("object", "other"), ("entry",))
    r.set("entry", word(add("other", 14)))
    def update():
        r.invoke_dict("set", "object", word(add("entry", 4)), word(add("entry", 6)))
        r.set("entry", word(add("entry", 8)))
    r.while_(("ne", "entry", 0), update)
    r.ret(); result.append(r.finish())

    r = SpecializedMap("copy", ("object",), ("result",))
    r.set("result", dc("new", word(add("object", 10)), word(add("object", 12)), word(add("object", 2))))
    r.invoke_dict("update", "result", "object"); r.ret("result"); result.append(r.finish())
    return result
