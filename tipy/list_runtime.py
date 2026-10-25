"""Emit ordinary Z80 list/allocator routines using the compiler's stack ABI.

Descriptors: data pointer, length, capacity, element width, reference count (words).
Heap blocks: payload size, free flag (words), followed by payload bytes.
The small emitter below is only an authoring aid; all operations run as Z80.
"""

import re

HEAP_BYTES = 8192
RUNTIME_TARGET = re.compile(r"\b(?:call|jp)\s+(__arr_[A-Za-z0-9_]+)\b")


def add(a, b): return ("+", a, b)
def sub(a, b): return ("-", a, b)
def word(address): return ("word", address)
def byte(address): return ("byte", address)
def call(name, *args): return ("call", "__arr_" + name, *args)


class Routine:
    def __init__(self, name, params=(), locals_=(), prefix="__arr_"):
        self.name = prefix + name
        self.code = [self.name + ":"]
        self.serial = 0
        self.slots = {name: 4 + 2 * (len(params) - i - 1) for i, name in enumerate(params)}
        self.slots.update({name: -2 * (i + 1) for i, name in enumerate(locals_)})
        self.emit("push ix", "ld ix,0", "add ix,sp")
        if locals_:
            self.emit(f"ld hl,{(-2 * len(locals_)) & 65535}", "add hl,sp", "ld sp,hl")

    def emit(self, *code): self.code.extend("        " + line for line in code)
    def label(self):
        self.serial += 1
        return f"{self.name}_L{self.serial}"
    def mark(self, label): self.code.append(label + ":")
    def expr(self, value):
        if isinstance(value, int):
            self.emit(f"ld hl,{value & 65535}")
        elif isinstance(value, str):
            if value == "$sp":
                self.emit("ld hl,0", "add hl,sp")
            elif value.startswith("@"):
                self.emit(f"ld hl,{value[1:]}")
            else:
                offset = self.slots[value]
                self.emit(f"ld l,(ix{offset:+d})", f"ld h,(ix{offset + 1:+d})")
        elif value[0] == "call":
            for argument in value[2:]:
                self.expr(argument)
                self.emit("push hl")
            self.emit(f"call {value[1]}")
            for _ in value[2:]: self.emit("pop de")
        elif value[0] in {"word", "byte"}:
            self.expr(value[1])
            if value[0] == "byte": self.emit("ld l,(hl)", "ld h,0")
            else: self.emit("ld e,(hl)", "inc hl", "ld d,(hl)", "ex de,hl")
        elif value[0] == "+" and value[1] == value[2]:
            # Runtime expression operands are pure except for explicit calls.
            # Never merge two calls: both evaluations may have side effects.
            def contains_call(item):
                return item == "$sp" or isinstance(item, tuple) and (item[0] == "call" or any(contains_call(v) for v in item[1:]))
            if contains_call(value[1]):
                self.expr(value[1]); self.emit("push hl")
                self.expr(value[2]); self.emit("ex de,hl", "pop hl", "add hl,de")
            else:
                self.expr(value[1])
                self.emit("add hl,hl")
        elif value[0] == "+" and isinstance(value[2], int) and 0 <= value[2] <= 3:
            # These expression flags are dead: conditions emit their own SUB.
            self.expr(value[1])
            self.emit(*(["inc hl"] * value[2]))
        else:
            self.expr(value[1])
            self.emit("push hl")
            self.expr(value[2])
            self.emit("ex de,hl", "pop hl")
            self.emit(*(["add hl,de"] if value[0] == "+" else ["or a", "sbc hl,de"]))

    def set(self, name, value):
        self.expr(value)
        offset = self.slots[name]
        self.emit(f"ld (ix{offset:+d}),l", f"ld (ix{offset + 1:+d}),h")

    def write(self, address, value, width=2):
        self.expr(address)
        self.emit("push hl")
        self.expr(value)
        self.emit("ex de,hl", "pop hl", "ld (hl),e")
        if width == 2: self.emit("inc hl", "ld (hl),d")

    def invoke(self, name, *args): self.expr(call(name, *args))
    def ret(self, value=0):
        self.expr(value)
        self.emit(f"jp {self.name}_return")
    def fail(self, code): self.emit(f"ld hl,{code}", "jp __arr_fail")

    def false_jump(self, condition, target):
        operation, *values = condition
        if operation in {"slt", "sge"}:
            self.expr(values[0]); self.emit("push hl")
            self.expr(values[1]); self.emit("ex de,hl", "pop hl")
            self.emit("ld a,h", "xor 128", "ld h,a", "ld a,d", "xor 128", "ld d,a",
                      "or a", "sbc hl,de", f"jp {'nc' if operation == 'slt' else 'c'},{target}")
            return
        if operation == "negative":
            self.expr(values[0])
            self.emit("ld a,h", "or a", f"jp p,{target}")
            return
        if operation in {"eq", "ne"} and values[1] == 0:
            self.expr(values[0])
            self.emit("ld a,h", "or l", f"jp {'nz' if operation == 'eq' else 'z'},{target}")
            return
        self.expr(sub(*values))
        branch = {"eq": "nz", "ne": "z", "ult": "nc", "uge": "c"}[operation]
        self.emit(f"jp {branch},{target}")

    def if_(self, condition, yes, no=None):
        otherwise, end = self.label(), self.label()
        self.false_jump(condition, otherwise)
        yes()
        self.emit(f"jp {end}")
        self.mark(otherwise)
        if no: no()
        self.mark(end)

    def while_(self, condition, body):
        start, end = self.label(), self.label()
        self.mark(start)
        self.false_jump(condition, end)
        body()
        self.emit(f"jp {start}")
        self.mark(end)

    def finish(self):
        self.mark(self.name + "_return")
        self.emit("ld sp,ix", "pop ix", "ret")
        return "\n".join(self.code)


def runtime_assembly(required=None):
    routines = []

    r = Routine("init")
    r.write("@__heap_start", HEAP_BYTES - 4)
    r.write(add("@__heap_start", 2), 1)
    r.write("@__list_error", 0)
    r.ret()
    routines.append(r.finish())

    r = Routine("len", ("object",))
    r.ret(word(add("object", 2)))
    routines.append(r.finish())

    r = Routine("malloc", ("need",), ("p", "size", "spare", "next"))
    r.if_(("uge", "@__heap_end", "$sp"), lambda: r.fail(3))
    r.if_(("ult", sub("$sp", "@__heap_end"), 256), lambda: r.fail(3))
    r.if_(("eq", "need", 0), lambda: r.set("need", 1))
    r.set("p", "@__heap_start")
    def allocate_block():
        r.set("size", word("p"))
        def free_block():
            def fits():
                r.set("spare", sub("size", "need"))
                def split():
                    r.write("p", "need")
                    r.set("next", add(add("p", 4), "need"))
                    r.write("next", sub("spare", 4))
                    r.write(add("next", 2), 1)
                r.if_(("uge", "spare", 6), split)
                r.write(add("p", 2), 0)
                r.ret(add("p", 4))
            r.if_(("uge", "size", "need"), fits)
        r.if_(("eq", word(add("p", 2)), 1), free_block)
        r.set("p", add(add("p", 4), "size"))
    r.while_(("ult", "p", "@__heap_end"), allocate_block)
    r.fail(3)
    routines.append(r.finish())

    r = Routine("free", ("pointer",), ("p", "next", "size", "merged"))
    r.if_(("eq", "pointer", 0), lambda: r.ret())
    r.write(sub("pointer", 2), 1)
    r.set("p", "@__heap_start")
    def coalesce():
        r.set("size", word("p"))
        r.set("next", add(add("p", 4), "size"))
        r.set("merged", 0)
        def has_next():
            def first_free():
                def second_free():
                    r.write("p", add(add("size", 4), word("next")))
                    r.set("merged", 1)
                r.if_(("eq", word(add("next", 2)), 1), second_free)
            r.if_(("eq", word(add("p", 2)), 1), first_free)
        r.if_(("ult", "next", "@__heap_end"), has_next)
        r.if_(("eq", "merged", 0), lambda: r.set("p", "next"))
    r.while_(("ult", "p", "@__heap_end"), coalesce)
    r.ret()
    routines.append(r.finish())

    r = Routine("new", ("width",), ("object",))
    r.set("object", call("malloc", 10))
    for offset in [0, 2, 4]: r.write(add("object", offset), 0)
    r.write(add("object", 6), "width")
    r.write(add("object", 8), 1)
    r.ret("object")
    routines.append(r.finish())

    r = Routine("retain", ("object",))
    r.if_(("ne", "object", 0), lambda: r.write(add("object", 8), add(word(add("object", 8)), 1)))
    r.ret("object")
    routines.append(r.finish())

    r = Routine("release", ("object",), ("refs",))
    r.if_(("eq", "object", 0), lambda: r.ret())
    r.set("refs", sub(word(add("object", 8)), 1))
    r.write(add("object", 8), "refs")
    def destroy():
        r.invoke("free", word("object"))
        r.invoke("free", "object")
    r.if_(("eq", "refs", 0), destroy)
    r.ret()
    routines.append(r.finish())

    r = Routine("bytes", ("object", "count"))
    r.if_(("eq", word(add("object", 6)), 2), lambda: r.ret(add("count", "count")))
    r.ret("count")
    routines.append(r.finish())

    for name, backwards in [("copy_bytes", False), ("move_back", True)]:
        r = Routine(name, ("destination", "source", "count"))
        r.if_(("eq", "count", 0), lambda: r.ret())
        r.expr("source"); r.emit("push hl")
        r.expr("destination"); r.emit("push hl")
        r.expr("count"); r.emit("ld b,h", "ld c,l", "pop de", "pop hl", "lddr" if backwards else "ldir")
        r.ret()
        routines.append(r.finish())

    r = Routine("reserve", ("object", "need"), ("capacity", "data", "old"))
    r.set("capacity", word(add("object", 4)))
    r.if_(("uge", "capacity", "need"), lambda: r.ret())
    r.if_(("uge", "need", 4097), lambda: r.fail(3))
    r.if_(("ult", "capacity", 4), lambda: r.set("capacity", 4))
    r.while_(("ult", "capacity", "need"), lambda: r.set("capacity", add("capacity", "capacity")))
    r.set("data", call("malloc", call("bytes", "object", "capacity")))
    r.set("old", word("object"))
    r.invoke("copy_bytes", "data", "old", call("bytes", "object", word(add("object", 2))))
    r.invoke("free", "old")
    r.write("object", "data")
    r.write(add("object", 4), "capacity")
    r.ret()
    routines.append(r.finish())

    r = Routine("normalize", ("object", "index"), ("length",))
    r.set("length", word(add("object", 2)))
    r.if_(("negative", "index"), lambda: r.set("index", add("index", "length")))
    r.if_(("uge", "index", "length"), lambda: r.fail(1))
    r.ret("index")
    routines.append(r.finish())

    r = Routine("address", ("object", "index"))
    r.set("index", call("normalize", "object", "index"))
    r.ret(add(word("object"), call("bytes", "object", "index")))
    routines.append(r.finish())

    r = Routine("get", ("object", "index"), ("pointer",))
    r.set("pointer", call("address", "object", "index"))
    r.if_(("eq", word(add("object", 6)), 1), lambda: r.ret(byte("pointer")))
    r.ret(word("pointer"))
    routines.append(r.finish())

    r = Routine("set", ("object", "index", "value"), ("pointer",))
    r.set("pointer", call("address", "object", "index"))
    r.if_(("eq", word(add("object", 6)), 1), lambda: r.write("pointer", "value", 1), lambda: r.write("pointer", "value"))
    r.ret()
    routines.append(r.finish())

    r = Routine("append", ("object", "value"), ("length", "pointer"))
    r.set("length", word(add("object", 2)))
    r.invoke("reserve", "object", add("length", 1))
    r.set("pointer", add(word("object"), call("bytes", "object", "length")))
    r.if_(("eq", word(add("object", 6)), 1), lambda: r.write("pointer", "value", 1), lambda: r.write("pointer", "value"))
    r.write(add("object", 2), add("length", 1))
    r.ret()
    routines.append(r.finish())

    r = Routine("insert", ("object", "index", "value"), ("length", "pointer", "width", "bytes"))
    r.set("length", word(add("object", 2)))
    r.set("width", word(add("object", 6)))
    r.if_(("negative", "index"), lambda: r.set("index", add("index", "length")))
    r.if_(("negative", "index"), lambda: r.set("index", 0))
    r.if_(("uge", "index", "length"), lambda: r.set("index", "length"))
    r.invoke("reserve", "object", add("length", 1))
    r.set("pointer", add(word("object"), call("bytes", "object", "index")))
    r.set("bytes", call("bytes", "object", sub("length", "index")))
    r.invoke("move_back", sub(add(add("pointer", "bytes"), "width"), 1), sub(add("pointer", "bytes"), 1), "bytes")
    r.if_(("eq", "width", 1), lambda: r.write("pointer", "value", 1), lambda: r.write("pointer", "value"))
    r.write(add("object", 2), add("length", 1))
    r.ret()
    routines.append(r.finish())

    r = Routine("pop", ("object", "index"), ("length", "value", "pointer", "width", "bytes"))
    r.set("length", word(add("object", 2)))
    r.if_(("eq", "length", 0), lambda: r.fail(2))
    r.set("index", call("normalize", "object", "index"))
    r.set("value", call("get", "object", "index"))
    r.set("width", word(add("object", 6)))
    r.set("pointer", call("address", "object", "index"))
    r.set("bytes", call("bytes", "object", sub(sub("length", "index"), 1)))
    r.invoke("copy_bytes", "pointer", add("pointer", "width"), "bytes")
    r.write(add("object", 2), sub("length", 1))
    r.ret("value")
    routines.append(r.finish())

    for name in ["index", "count"]:
        r = Routine(name, ("object", "value"), ("i", "count", "length"))
        r.set("i", 0); r.set("count", 0)
        r.set("length", word(add("object", 2)))
        def search():
            r.if_(("eq", call("get", "object", "i"), "value"),
                  (lambda: r.ret("i")) if name == "index" else (lambda: r.set("count", add("count", 1))))
            r.set("i", add("i", 1))
        r.while_(("ult", "i", "length"), search)
        if name == "index": r.fail(4)
        else: r.ret("count")
        routines.append(r.finish())

    r = Routine("remove", ("object", "value"), ("index",))
    r.set("index", call("index", "object", "value"))
    r.invoke("pop", "object", "index")
    r.ret()
    routines.append(r.finish())

    r = Routine("clear", ("object",))
    r.invoke("free", word("object"))
    for offset in [0, 2, 4]: r.write(add("object", offset), 0)
    r.ret()
    routines.append(r.finish())

    r = Routine("extend", ("object", "other"), ("length", "added", "destination", "bytes"))
    r.set("length", word(add("object", 2)))
    r.set("added", word(add("other", 2)))
    r.invoke("reserve", "object", add("length", "added"))
    r.set("destination", add(word("object"), call("bytes", "object", "length")))
    r.set("bytes", call("bytes", "other", "added"))
    r.invoke("copy_bytes", "destination", word("other"), "bytes")
    r.write(add("object", 2), add("length", "added"))
    r.ret()
    routines.append(r.finish())

    r = Routine("copy", ("object",), ("result", "length"))
    r.set("result", call("new", word(add("object", 6))))
    r.set("length", word(add("object", 2)))
    r.invoke("reserve", "result", "length")
    r.invoke("copy_bytes", word("result"), word("object"), call("bytes", "object", "length"))
    r.write(add("result", 2), "length")
    r.ret("result")
    routines.append(r.finish())

    r = Routine("reverse", ("object",), ("i", "j", "value"))
    r.set("i", 0)
    r.if_(("eq", word(add("object", 2)), 0), lambda: r.ret())
    r.set("j", sub(word(add("object", 2)), 1))
    def swap():
        r.set("value", call("get", "object", "i"))
        r.invoke("set", "object", "i", call("get", "object", "j"))
        r.invoke("set", "object", "j", "value")
        r.set("i", add("i", 1)); r.set("j", sub("j", 1))
    r.while_(("ult", "i", "j"), swap)
    r.ret()
    routines.append(r.finish())

    from .python_runtime import builtin_routines
    routines.extend(builtin_routines())
    routines.append("""__arr_fail:
        ld (__list_error),hl
        ld sp,(__list_main_sp)
        ld ix,(__list_main_ix)
        ret""")
    if required is None:
        return "\n\n".join(routines)
    by_name = {routine.split(":", 1)[0]: routine for routine in routines}
    reachable = set()
    pending = list(required)
    while pending:
        name = pending.pop()
        if name in reachable or name not in by_name:
            continue
        reachable.add(name)
        pending.extend(RUNTIME_TARGET.findall(by_name[name]))
    return "\n\n".join(routine for name, routine in by_name.items() if name in reachable)
