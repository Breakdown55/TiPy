"""Benchmark-only circle alternatives; does not change the compiler default."""
from pathlib import Path
import subprocess
import types


def install(variant):
    if variant not in {"original", "bresenham", "full_bresenham"}:
        raise ValueError("Unknown circle benchmark variant")
    import tipy.shape_runtime as current
    root = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(
        ["git", "show", "f1f86cd:tipy/shape_runtime.py"], cwd=root, text=True, encoding="utf-8")
    original = types.ModuleType("tipy._circle_benchmark_reference")
    original.__package__ = "tipy"
    exec(compile(source, "original_shape_runtime.py", "exec"), original.__dict__)
    if variant in {"bresenham", "full_bresenham"}:
        curve = original._curve
        def replacement(ellipse):
            if ellipse:
                return curve(True)
            reference = curve(False).replace("__gfx_circle", "__gfx_original_circle").replace("__gfx_original_circle_extent", "__gfx_circle_extent")
            return bresenham(original, variant == "full_bresenham") + "\n" + reference
        original._curve = replacement
    current.assembly = original.assembly


def bresenham(module, full=False):
    """Standard eight-way midpoint/Bresenham circle, emitted as native Z80."""
    r = module.ShapeRoutine("circle", ("cx", "cy", "radius", "filled"), ("x", "y", "error"))
    add, sub, invoke = module.add, module.sub, module.invoke
    r.if_(("negative", "radius"), lambda: r.ret())
    r.if_(("uge", "radius", 8192 if full else 64), lambda: (r.expr(("call", "__gfx_circle_full" if full else "__gfx_original_circle", "cx", "cy", "radius", "filled")), r.ret()))
    bounds = (("cx",sub(0,"radius"),add(95,"radius")),("cy",sub(0,"radius"),add(63,"radius"))) if full else (("cx", -63, 158), ("cy", -63, 126))
    for value, low, high in bounds:
        r.if_(("slt", value, low), lambda: r.ret())
        r.if_(("slt", high, value), lambda: r.ret())
    r.set("x", 0); r.set("y", "radius"); r.set("error", sub(3, add("radius", "radius")))
    def four(value):
        return add(add(value, value), add(value, value))
    def point(x, y):
        r.expr(x); r.emit("push hl"); r.expr(y)
        r.emit("ex de,hl", "pop hl", "call __gfx_ink_pixel")
    def filled():
        for dx, dy in (("x", "y"), ("y", "x")):
            for y in (add("cy", dy), sub("cy", dy)):
                r.expr(invoke("h_line", sub("cx", dx), y, add("cx", dx), y))
    def outlined():
        for dx, dy in (("x", "y"), ("y", "x")):
            for x in (add("cx", dx), sub("cx", dx)):
                for y in (add("cy", dy), sub("cy", dy)):
                    point(x, y)
    def row():
        r.if_(("ne", "filled", 0), filled, outlined)
        def diagonal():
            r.set("error", add("error", add(four(sub("x", "y")), 10)))
            r.set("y", sub("y", 1))
        r.if_(("slt", "error", 0), lambda: r.set("error", add("error", add(four("x"), 6))), diagonal)
        r.set("x", add("x", 1))
    r.while_(("slt", "x", add("y", 1)), row)
    r.ret()
    return r.finish() + ('\n' + full_bresenham() if full else '')


def full_bresenham():
    from tipy.camera_math import NativeRoutine, invoke
    from tipy.shape_runtime import words
    from tipy.list_runtime import add, sub
    r=NativeRoutine('circle_full',('cx','cy','radius','filled'),('x','y','v')+words('error',2)+words('delta',2))
    for center,limit in [('cx',96),('cy',64)]:
        r.set('v',invoke('sat_add',center,'radius'));r.if_(('slt','v',0),lambda:r.ret())
        r.set('v',invoke('sat_add',center,sub(0,'radius')));r.if_(('sge','v',limit),lambda:r.ret())
    r.set('x',0);r.set('y','radius');r.set('error0',3);r.set('error1',0)
    r.set('delta0','radius');r.set('delta1',0)
    r.binary('error','error','delta',count=2,subtract=True);r.binary('error','error','delta',count=2,subtract=True)
    def coordinate(center,offset,negative):return invoke('sat_add',center,sub(0,offset) if negative else offset)
    def filled():
        for dx,dy in [('x','y'),('y','x')]:
            for negative in [False,True]:
                y=coordinate('cy',dy,negative)
                r.expr(invoke('styled_h_line',coordinate('cx',dx,True),y,coordinate('cx',dx,False),y))
    def outlined():
        for dx,dy in [('x','y'),('y','x')]:
            for nx in [False,True]:
                for ny in [False,True]:
                    r.expr(coordinate('cx',dx,nx));r.emit('push hl');r.expr(coordinate('cy',dy,ny));r.emit('ex de,hl','pop hl','call __gfx_ink_pixel')
    def delta(value,constant):
        r.set('delta0',value);r.set('delta1',0)
        r.if_(('negative','delta0'),lambda:r.set('delta1',65535))
        for _ in range(2):
            for i,name in enumerate(['delta0','delta1']):
                offset=r.slots[name];r.emit(f'{"sla" if i==0 else "rl"} (ix{offset:+d})',f'rl (ix{offset+1:+d})')
        r.expr('delta0');r.emit(f'ld de,{constant}','add hl,de');r._store('delta0')
        r.expr('delta1');r.emit('ld de,0','adc hl,de');r._store('delta1')
        r.binary('error','error','delta',count=2)
    def row():
        r.if_(('ne','filled',0),filled,outlined)
        def diagonal():delta(sub('x','y'),10);r.set('y',sub('y',1))
        r.if_(('negative','error1'),lambda:delta('x',6),diagonal)
        r.set('x',add('x',1))
    r.while_(('ult','x',add('y',1)),row);r.ret()
    return r.finish()
