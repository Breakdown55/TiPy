"""96x64 monochrome software rasterizer; render alone updates the LCD.

HL=x and DE=y. Ignore off-screen coordinates before any byte truncation.
Scratch registers are AF, BC, DE, HL; IX and IY are preserved.
"""

from .list_runtime import Routine, add, sub, word, byte


class GraphicsRoutine(Routine):
    def __init__(self, name, params=(), locals_=()):
        super().__init__(name, params, locals_, prefix="__gfx_")

    def finish(self):
        from .optimizer import optimize,relax_graphics_branches
        return relax_graphics_branches(optimize(super().finish()))

    def false_jump(self, condition, target):
        if condition[0] not in {"slt", "sge"}:
            return super().false_jump(condition, target)
        self.expr(condition[1]); self.emit("push hl")
        self.expr(condition[2]); self.emit("ex de,hl", "pop hl")
        self.emit("ld a,h", "xor 128", "ld h,a", "ld a,d", "xor 128", "ld d,a",
                  "or a", "sbc hl,de", f"jp {'nc' if condition[0] == 'slt' else 'c'},{target}")

    def pixel(self, x, y):
        self.expr(x); self.emit("push hl")
        self.expr(y); self.emit("ex de,hl", "pop hl", "call __gfx_pixel_on")

    def order(self, low, high):
        def swap():
            self.expr(low); self.emit("push hl")
            self.set(low, high)
            self.emit("pop hl")
            self._store(high)
        self.if_(("slt", high, low), swap)

    def _store(self, name):
        offset = self.slots[name]
        self.emit(f"ld (ix{offset:+d}),l", f"ld (ix{offset + 1:+d}),h")

    def clip_interval(self, low, high, limit):
        self.order(low, high)
        self.if_(("slt", high, 0), lambda: self.ret())
        self.if_(("sge", low, limit), lambda: self.ret())
        self.if_(("slt", low, 0), lambda: self.set(low, 0))
        self.if_(("sge", high, limit), lambda: self.set(high, limit - 1))


def _axis_line(vertical,styled=False):
    r=GraphicsRoutine('v_line' if vertical else 'h_line',('x1','y1','x2','y2'),('mask','first'))
    fixed,other,low,high,limit=('x1','x2','y1','y2',64) if vertical else ('y1','y2','x1','x2',96)
    r.if_(('ne',fixed,other),lambda:r.ret());r.if_(('uge',fixed,96 if vertical else 64),lambda:r.ret());r.clip_interval(low,high,limit)
    def blend():
        if styled:
            set_,done=r.label(),r.label();r.emit('ld e,a','ld a,(__gfx_ink)','or a','ld a,e',f'jr nz,{set_}','cpl','and (hl)',f'jr {done}');r.mark(set_);r.emit('or (hl)');r.mark(done)
        else:r.emit('or (hl)')
        r.emit('ld (hl),a')
    def shift_mask(value,left=False):
        r.expr(value);r.emit('ld a,l')
        if left:r.emit('cpl')
        r.emit('and 7','ld b,a','ld a,255' if not vertical else 'ld a,128','inc b')
        loop,ready=r.label(),r.label();r.emit(f'jr {ready}');r.mark(loop);r.emit('sla a' if left else 'srl a');r.mark(ready);r.emit(f'djnz {loop}')
    if vertical:
        shift_mask(fixed);r.emit('ld c,a')
    else:
        shift_mask(low);r.emit('ld l,a','ld h,0');r._store('mask');shift_mask(high,True);r.emit('ld c,a')
    r.expr(low if vertical else fixed);r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,0x9340','add hl,de','push hl')
    r.expr(fixed if vertical else low);r.emit('srl l','srl l','srl l');r._store('first');r.emit('pop de','add hl,de','push hl')
    if vertical:
        r.expr(add(sub(high,low),1));r.emit('ld b,l','ld d,c','pop hl')
        loop,advance=r.label(),r.label();r.mark(loop);r.emit('ld a,d');blend()
        r.emit('ld a,l','add a,12','ld l,a',f'jr nc,{advance}','inc h');r.mark(advance);r.emit(f'djnz {loop}');r.ret()
    else:
        r.expr(high);r.emit('srl l','srl l','srl l','push hl');r.expr('first');r.emit('ex de,hl','pop hl','or a','sbc hl,de','ld b,l');r.expr('mask');r.emit('ld d,l','pop hl')
        multiple,middle,last=r.label(),r.label(),r.label();r.emit('ld a,b','or a',f'jr nz,{multiple}','ld a,d','and c');blend();r.ret()
        r.mark(multiple);r.emit('ld a,d');blend();r.emit('inc hl','dec b',f'jr z,{last}')
        if styled:
            r.emit('ld a,(__gfx_ink)','neg')
        else:r.emit('ld a,255')
        r.mark(middle);r.emit('ld (hl),a','inc hl',f'djnz {middle}');r.mark(last);r.emit('ld a,c');blend();r.ret()
    return r.finish()


def _line(styled=False):
    # Unsigned magnitudes cover the full distance between any i16 endpoints.
    # The fractional error never exceeds the major delta; carry is handled
    # explicitly when adding the minor delta, avoiding signed overflow.
    r = GraphicsRoutine("line", ("x1", "y1", "x2", "y2"), ("dx", "dy", "sx", "sy", "error", "swapped"))
    for first, second, limit in [("x1", "x2", 96), ("y1", "y2", 64)]:
        r.if_(("slt", first, 0), lambda: r.if_(("slt", second, 0), lambda: r.ret()))
        r.if_(("sge", first, limit), lambda: r.if_(("sge", second, limit), lambda: r.ret()))
    if not styled:
        general=r.label()
        for value,limit in (('x1',96),('x2',96),('y1',64),('y2',64)):
            r.if_(('uge',value,limit),lambda:r.emit(f'jp {general}'))
        if not styled:r.emit('ld a,(__gfx_ink)','push af','ld a,1','ld (__gfx_ink),a')
        r.expr(('call','__gfx_wf_raster','x1','y1','x2','y2'))
        if not styled:r.emit('pop af','ld (__gfx_ink),a')
        r.ret();r.mark(general)
    for axis in ["x", "y"]:
        r.set("s" + axis, 1)
        r.set("d" + axis, sub(axis + "2", axis + "1"))
        def negative(axis=axis):
            r.set("s" + axis, -1)
            r.set("d" + axis, sub(0, "d" + axis))
        r.if_(("slt", axis + "2", axis + "1"), negative)
    r.set("swapped", 0)
    def transpose():
        r.set("swapped", 1)
        for a, b in [("x1", "y1"), ("x2", "y2"), ("dx", "dy"), ("sx", "sy")]:
            r.expr(a); r.emit("push hl"); r.set(a, b)
            r.emit("pop hl"); r._store(b)
    r.if_(("ult", "dx", "dy"), transpose)
    r.expr("dx"); r.emit("srl h", "rr l"); r._store("error")
    start, straight, minor, stepped = r.label(), r.label(), r.label(), r.label()
    r.mark(start)
    r.if_(("eq", "swapped", 0), lambda: r.pixel("x1", "y1"), lambda: r.pixel("y1", "x1"))
    r.if_(("eq", "x1", "x2"), lambda: r.ret())
    r.set("x1", add("x1", "sx"))
    r.expr("error"); r.emit("push hl"); r.expr("dy")
    r.emit("ex de,hl", "pop hl", "add hl,de", f"jp c,{minor}", "push hl")
    r.expr("dx"); r.emit("ex de,hl", "pop hl", "or a", "sbc hl,de", f"jp nc,{stepped}", "add hl,de", f"jp {straight}")
    r.mark(minor)
    r.emit("push hl"); r.expr("dx"); r.emit("ex de,hl", "pop hl", "or a", "sbc hl,de")
    r.mark(stepped)
    r._store("error")
    r.set("y1", add("y1", "sy"))
    r.emit(f"jp {start}")
    r.mark(straight)
    r._store("error")
    r.emit(f"jp {start}")
    return r.finish()


def _rectangle():
    r = GraphicsRoutine("rectangle", ("x1", "x2", "y1", "y2", "width", "filled"),
                        ("left", "right", "top", "bottom", "x", "y"))
    r.order("x1", "x2"); r.order("y1", "y2")
    r.if_(("eq", "filled", 0), lambda: r.if_(("slt", "width", 1), lambda: r.ret()))
    for low, high, first, second, limit in [("left", "right", "x1", "x2", 96), ("top", "bottom", "y1", "y2", 64)]:
        r.set(low, first); r.set(high, second); r.clip_interval(low, high, limit)
    r.set("y", "top")
    def filled_rows():
        def span():
            r.expr(('call','__gfx_h_line','left','y','right','y'));r.set('y',add('y',1))
        r.while_(('slt','y',add('bottom',1)),span);r.ret()
    r.if_(('ne','filled',0),filled_rows)
    row, column, draw, next_pixel = r.label(), r.label(), r.label(), r.label()
    r.mark(row)
    r.set("x", "left")
    r.mark(column)
    # Distances are unsigned and exact even when a corner is -32768.
    r.false_jump(("eq", "filled", 0), draw)
    for distance in [sub("x", "x1"), sub("x2", "x"), sub("y", "y1"), sub("y2", "y")]:
        skip = r.label()
        r.false_jump(("ult", distance, "width"), skip)
        r.emit(f"jp {draw}")
        r.mark(skip)
    r.emit(f"jp {next_pixel}")
    r.mark(draw); r.pixel("x", "y")
    r.mark(next_pixel)
    r.if_(("ne", "x", "right"), lambda: (r.set("x", add("x", 1)), r.emit(f"jp {column}")))
    r.if_(("eq", "y", "bottom"), lambda: r.ret())
    r.set("y", add("y", 1)); r.emit(f"jp {row}")
    return r.finish()


def _pixel_assembly(used):
    modes = {"pixel_off": 0, "pixel_on": 1, "flip_pixel": 2}
    wrappers = [f"__gfx_{name}:\n        ld a,{mode}\n        jp __gfx_pixel"
                for name, mode in modes.items() if name in used]
    return "\n".join(wrappers) + "\n" + """__gfx_pixel:
        ld c,a
        ld a,h
        or d
        ret nz
        ld a,l
        cp 96
        ret nc
        ld a,e
        cp 64
        ret nc
        ld b,l
        ld l,e
        ld h,0
        add hl,hl
        add hl,hl
        push hl
        add hl,hl
        pop de
        add hl,de
        ld a,b
        rrca
        rrca
        rrca
        and 31
        ld e,a
        ld d,0
        add hl,de
        ld de,0x9340
        add hl,de
        ld a,b
        and 7
        ld b,a
        inc b
        ld a,1
__gfx_mask:
        rrca
        djnz __gfx_mask
        ld e,a
        ld a,c
        or a
        jp z,__gfx_off
        dec a
        ld a,e
        jp z,__gfx_on
        xor (hl)
        jp __gfx_store
__gfx_off:
        ld a,e
        cpl
        and (hl)
        jp __gfx_store
__gfx_on:
        or (hl)
__gfx_store:
        ld (hl),a
        ret
"""


RENDER = """__gfx_render:
        push ix
        push iy
        rst 0x28
        dw 0x486a
        pop iy
        pop ix
        ret
"""

CLEAR = """__gfx_clear:
        ld hl,0x9340
        ld de,0x9341
        ld bc,767
        ld (hl),0
        ldir
        ret
"""


def _build_row_mask():
    r = GraphicsRoutine("build_row_mask", ("object", "mask", "parity"), ("data", "length", "width", "index", "value"))
    r.expr("mask")
    r.emit("ld (hl),0", "push hl", "inc hl", "ex de,hl", "pop hl", "ld bc,11", "ldir")
    r.set("data", word("object")); r.set("length", word(add("object", 2)))
    r.set("width", word(add("object", 6))); r.set("index", 0); r.set("value", 0)
    def add_pixel():
        r.if_(("eq", "width", 1), lambda: r.set("value", byte("data")), lambda: r.set("value", word("data")))
        r.set("data", add("data", "width"))
        r.expr("value"); r.emit("push hl")
        r.expr("mask"); r.emit("ex de,hl", "pop hl", "ld a,(ix+4)", "call __gfx_row_mask_bit")
        r.set("index", add("index", 1))
    r.while_(("ult", "index", "length"), add_pixel)
    r.ret("value")
    return r.finish() + "\n" + """__gfx_row_mask_bit:
        ld c,a
        ld a,h
        or a
        ret nz
        ld a,l
        cp 96
        ret nc
        ld b,l
        rrca
        rrca
        rrca
        and 31
        ld l,a
        ld h,0
        add hl,de
        ld a,b
        and 7
        ld b,a
        inc b
        ld a,1
__gfx_row_mask_rotate:
        rrca
        djnz __gfx_row_mask_rotate
        ld e,a
        ld a,c
        or a
        ld a,e
        jp nz,__gfx_row_mask_flip
        or (hl)
        jp __gfx_row_mask_store
__gfx_row_mask_flip:
        xor (hl)
__gfx_row_mask_store:
        ld (hl),a
        ret
"""


def _apply_row(name):
    operation = {"or_row": "or (hl)", "and_row": "cpl\n        and (hl)", "xor_row": "xor (hl)"}[name]
    return f"""__gfx_{name}:
        ld b,12
__gfx_{name}_loop:
        ld a,(de)
        {operation}
        ld (hl),a
        inc de
        inc hl
        djnz __gfx_{name}_loop
        ret
"""


def graphics_assembly(used):
    used=set(used)
    routines = []
    camera_entries={name for name in used if name.startswith(('cam_','mat_','model_'))}
    if 'line' in used:camera_entries.add('wf_raster');used.add('ink')
    if camera_entries:
        from .camera_math import routines as math_routines,link
        from .matrix_runtime import routines as matrix_routines
        from .camera_runtime import routines as camera_routines
        from .wireframe_runtime import routines as wireframe_routines
        capacity=max((int(n.split(':')[1]) for n in used if n.startswith('batch_capacity:')),default=64)
        groups=math_routines()+matrix_routines()+camera_routines(batch_capacity=capacity)+wireframe_routines(capacity)
        roots={'__gfx_'+name for name in camera_entries}
        native=link(groups,roots)
        routines.append(native)
        if '__gfx_sin_raw' in native:used.add('sin_raw')
        if '__gfx_sat_add' in native:used.add('sat_add')
        if '__gfx_polygon' in native:used.add('polygon')
        if '__gfx_styled_line' in native:used.add('styled_line')
    styled=bool(used & {'ink','stroke','circle','ellipse','polygon','polyline','styled_rectangle','styled_line'})
    if used & {'background','sin_raw','sat_add','stroke','circle','ellipse','polygon','polyline','styled_line','styled_rectangle'}:
        from .shape_runtime import assembly
        routines.append(assembly(used))
        if used & {'circle','ellipse','polygon','styled_rectangle'}:routines.append(_axis_line(False,True).replace('__gfx_h_line','__gfx_styled_h_line'))
        if used & {'polygon','polyline','stroke','styled_line'}:routines.append(_line(True).replace('__gfx_line','__gfx_styled_line').replace('call __gfx_pixel_on','call __gfx_ink_pixel'))
    if 'styled_rectangle' in used:routines.append(_rectangle().replace('__gfx_rectangle','__gfx_styled_rectangle').replace('call __gfx_pixel_on','call __gfx_ink_pixel').replace('call __gfx_h_line','call __gfx_styled_h_line'))
    if styled:routines.append('__gfx_ink_pixel:\n        ld a,(__gfx_ink)\n        jp __gfx_pixel\n__gfx_ink: db 1');used.add('pixel_on')
    if "build_row_mask" in used:
        routines.append(_build_row_mask())
    for name in ("or_row", "and_row", "xor_row"):
        if name in used:
            routines.append(_apply_row(name))
    if "clear" in used:
        routines.append(CLEAR)
    if "render" in used:
        routines.append(RENDER)
    if "line" in used:
        routines.append(_line())
    if "h_line" in used or 'rectangle' in used:
        routines.append(_axis_line(False))
    if "v_line" in used:
        routines.append(_axis_line(True))
    if "rectangle" in used:
        routines.append(_rectangle())
    pixels = used & {"pixel_on", "pixel_off", "flip_pixel"}
    if used & {"line", "h_line", "v_line", "rectangle"}:
        pixels |= {"pixel_on"}
    if pixels:
        routines.append(_pixel_assembly(pixels))
    return "\n\n".join(routines)
