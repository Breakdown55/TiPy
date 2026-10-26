"""Demand-linked native scalar helpers beyond the basic ALU operations."""
from .list_runtime import Routine,add,sub,word,call


def routines():
    out=[]
    for name in ("div","mod"):
        r=Routine("scalar_"+name,("a","b"),prefix="__obj_")
        r.expr("a");r.emit("push hl");r.expr("b");r.emit("ex de,hl","pop hl",f"call __math_{name}",f"jp {r.name}_return");out.append(r.finish())
    r=Routine("mod_multiply",("a","b","modulus"),("result",),prefix="__obj_")
    r.set("result",0)
    def reduce(name):r.if_(("uge",name,"modulus"),lambda:r.set(name,sub(name,"modulus")))
    def step():
        r.expr("b");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}")
        r.set("result",add("result","a"));reduce("result");r.mark(skip)
        r.set("a",add("a","a"));reduce("a")
        r.expr("b");r.emit("srl h","rr l");o=r.slots["b"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.while_(("ne","b",0),step);r.ret("result");out.append(r.finish())
    r=Routine("pow_mod",("base","exponent","modulus"),("magnitude","result","negative"),prefix="__obj_")
    r.if_(("eq","modulus",0),lambda:r.fail(14));r.if_(("negative","exponent"),lambda:r.fail(14))
    r.set("magnitude","modulus");r.set("negative",0)
    r.if_(("negative","modulus"),lambda:(r.set("magnitude",sub(0,"modulus")),r.set("negative",1)))
    r.set("base",("call","__obj_scalar_mod","base","modulus"))
    r.if_(("negative","base"),lambda:r.set("base",add("base","magnitude")))
    r.set("result",1);r.if_(("eq","magnitude",1),lambda:r.ret(0))
    def exponent():
        r.expr("exponent");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}")
        r.set("result",("call","__obj_mod_multiply","result","base","magnitude"));r.mark(skip)
        r.set("base",("call","__obj_mod_multiply","base","base","magnitude"))
        r.expr("exponent");r.emit("srl h","rr l");o=r.slots["exponent"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.while_(("ne","exponent",0),exponent)
    r.if_(("ne","negative",0),lambda:r.if_(("ne","result",0),lambda:r.set("result",sub("result","magnitude"))))
    r.ret("result");out.append(r.finish())
    r=Routine("true_divide",("a","b"),("whole","remainder","divisor","fraction","bits"),prefix="__obj_")
    r.set("whole",("call","__obj_scalar_div","a","b"))
    r.if_(("slt","whole",-128),lambda:r.fail(14));r.if_(("sge","whole",128),lambda:r.fail(14))
    r.set("remainder",("call","__obj_scalar_mod","a","b"));r.set("divisor","b")
    for v in ("remainder","divisor"):r.if_(("negative",v),lambda v=v:r.set(v,sub(0,v)))
    r.set("fraction",0);r.set("bits",8)
    def fraction():
        r.set("remainder",add("remainder","remainder"));r.set("fraction",add("fraction","fraction"))
        r.if_(("uge","remainder","divisor"),lambda:(r.set("remainder",sub("remainder","divisor")),r.set("fraction",add("fraction",1))))
        r.set("bits",sub("bits",1))
    r.while_(("ne","bits",0),fraction)
    r.expr("whole");r.emit("ld h,l","ld l,0","push hl");r.expr("fraction");r.emit("pop de","add hl,de",f"jp {r.name}_return");out.append(r.finish())
    r=Routine("round_fixed",("value",),("whole","fraction"),prefix="__obj_")
    r.expr("value");r.emit("ld l,h","ld h,0","bit 7,l");positive=r.label();r.emit(f"jp z,{positive}","ld h,255");r.mark(positive)
    o=r.slots["whole"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.expr("value");r.emit("ld h,0");o=r.slots["fraction"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.if_(("uge","fraction",129),lambda:r.set("whole",add("whole",1)))
    def tie():
        r.expr("whole");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}");r.set("whole",add("whole",1));r.mark(skip)
    r.if_(("eq","fraction",128),tie);r.ret("whole");out.append(r.finish())
    return out
