"""Native IEEE binary32 arithmetic with guard/round/sticky bits.

Float values use immutable four-byte payloads and the word-sized object ABI.
No operation calls a host floating-point implementation during execution.
"""
from .wide_runtime import WideRoutine
from .list_runtime import Routine,add,sub,word,call


class FloatRoutine(WideRoutine):
    def __init__(self,name,params=(),pairs=(),locals_=()):super().__init__(name,params,pairs,locals_,prefix="__obj_f32_")
    def unpack(self,name,value):
        self.box(name,value);self.set(name+"_sign",0)
        self.if_(("negative",name+"_hi"),lambda:self.set(name+"_sign",1))
        self.expr(name+"_hi");self.emit("ld a,h","and 127","ld h,a","ld a,l","and 128","ld l,a")
        for _ in range(7):self.emit("srl h","rr l")
        o=self.slots[name+"_exp"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        self.expr(name+"_hi");self.emit("ld a,l","and 127","ld l,a","ld h,0")
        o=self.slots[name+"_hi"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        self.if_(("eq",name+"_exp",0),lambda:self.set(name+"_exp",1),lambda:self.if_(("ne",name+"_exp",255),lambda:self.set(name+"_hi",add(name+"_hi",128))))
    def sticky_right(self,name):
        self.shift(name,"right");done=self.label();self.emit(f"jp nc,{done}")
        self.expr(name+"_lo");self.emit("set 0,l");o=self.slots[name+"_lo"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h");self.mark(done)
    def nonzero(self,name,yes):
        self.zero(name);done=self.label();self.emit("ld a,h","or l",f"jp z,{done}");yes();self.mark(done)
    def bits(self,low,high):self.ret(("call","__obj_f32_new",low,high))
    def special(self,kind,sign=0):
        high={"zero":0,"inf":32640,"nan":32704}[kind]
        if kind!="nan":self.if_(("ne",sign,0),lambda:self.bits(0,high+32768))
        self.bits(0,high)
    def packed(self,name,exponent,sign):self.ret(("call","__obj_f32_pack",name+"_lo",name+"_hi",exponent,sign))
    def normalize(self,name):
        # Normalize a nonzero 24-bit significand, including subnormal inputs.
        self.while_(("ult",name+"_hi",128),lambda:(self.shift(name,"left"),self.set(name+"_exp",sub(name+"_exp",1))))


def routines():
    out=[]
    r=Routine("new",("low","high"),("result",),prefix="__obj_f32_")
    r.set("result",("call","__obj_i32_new","low","high"));r.write(add("result",6),32762);r.ret("result");out.append(r.finish())
    r=FloatRoutine("pack",("low","high","exponent","negative"),("m",),("round","encoded","top"))
    r.set("m_lo","low");r.set("m_hi","high");r.zero("m");nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{nonzero}");r.special("zero","negative");r.mark(nonzero)
    r.while_(("uge","m_hi",2048),lambda:(r.sticky_right("m"),r.set("exponent",add("exponent",1))))
    begin,end=r.label(),r.label();r.mark(begin)
    r.false_jump(("ult","m_hi",1024),end);r.false_jump(("sge","exponent",2),end)
    r.shift("m","left");r.set("exponent",sub("exponent",1));r.emit(f"jp {begin}");r.mark(end)
    r.while_(("slt","exponent",1),lambda:(r.sticky_right("m"),r.set("exponent",add("exponent",1))))
    r.expr("m_lo");r.emit("ld a,l","and 7","ld l,a","ld h,0");o=r.slots["round"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    roundup,rounded=r.label(),r.label();r.false_jump(("ult","round",5),roundup)
    r.false_jump(("eq","round",4),rounded);r.expr("m_lo");r.emit("bit 3,l",f"jp z,{rounded}");r.mark(roundup)
    r.registers("m");r.emit("ld bc,8","add hl,bc");no_carry=r.label();r.emit(f"jp nc,{no_carry}","inc de");r.mark(no_carry);r.save_registers("m");r.mark(rounded)
    for _ in range(3):r.shift("m","right")
    r.if_(("uge","m_hi",256),lambda:(r.shift("m","right"),r.set("exponent",add("exponent",1))))
    r.if_(("sge","exponent",255),lambda:r.special("inf","negative"))
    r.set("encoded","exponent")
    r.if_(("eq","exponent",1),lambda:r.if_(("ult","m_hi",128),lambda:r.set("encoded",0)))
    r.expr("m_hi");r.emit("ld a,l","and 127","ld l,a","ld h,0");o=r.slots["top"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.expr("encoded")
    for _ in range(7):r.emit("add hl,hl")
    o=r.slots["encoded"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.set("top",add("top","encoded"));r.if_(("ne","negative",0),lambda:r.set("top",add("top",32768)))
    r.bits("m_lo","top");out.append(r.finish())
    r=FloatRoutine("from_integer",("value","kind"),("m",),("negative","exponent"))
    r.set("negative",0);r.set("exponent",153)
    def wide():r.box("m","value")
    def small():
        r.set("m_lo","value");r.set("m_hi",0);r.if_(("negative","value"),lambda:r.set("m_hi",65535))
    r.if_(("eq","kind",2),wide,small)
    r.if_(("eq","kind",1),lambda:r.set("exponent",145))
    r.if_(("negative","m_hi"),lambda:(r.negate("m"),r.set("negative",1)))
    r.packed("m","exponent","negative");out.append(r.finish())
    for op in ("add","subtract"):
        r=FloatRoutine(op,("left","right"),("a","b","m"),("a_exp","a_sign","b_exp","b_sign","count","sign","exponent"))
        r.unpack("a","left");r.unpack("b","right")
        if op=="subtract":r.set("b_sign",sub(1,"b_sign"))
        for name in ("a","b"):
            def special(name=name):
                r.nonzero(name,lambda:r.special("nan"))
                other="b" if name=="a" else "a"
                def both_inf():
                    r.nonzero(other,lambda:r.special("nan"));r.if_(("ne","a_sign","b_sign"),lambda:r.special("nan"))
                r.if_(("eq",other+"_exp",255),both_inf);r.special("inf",name+"_sign")
            r.if_(("eq",name+"_exp",255),special)
            for _ in range(3):r.shift(name,"left")
        r.set("exponent","a_exp")
        def align(small,big):
            r.set("exponent",big+"_exp");r.set("count",sub(big+"_exp",small+"_exp"))
            r.if_(("uge","count",32),lambda:r.set("count",32))
            r.while_(("ne","count",0),lambda:(r.sticky_right(small),r.set("count",sub("count",1))))
        r.if_(("slt","a_exp","b_exp"),lambda:align("a","b"),lambda:align("b","a"))
        r.set("sign","a_sign")
        def different():
            # Default to b-a; choose a-b when its magnitude is greater.
            r.binary("subtract","m","b","a");r.set("sign","b_sign")
            r.ge("a","b",lambda:(r.binary("subtract","m","a","b"),r.set("sign","a_sign")))
            r.zero("m");nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{nonzero}");r.set("sign",0);r.mark(nonzero)
        r.if_(("eq","a_sign","b_sign"),lambda:r.binary("add","m","a","b"),different)
        r.packed("m","exponent","sign");out.append(r.finish())
    r=FloatRoutine("multiply",("left","right"),("a","b","p_low","p_high","x_low","x_high","y"),("a_exp","a_sign","b_exp","b_sign","sign","exponent","bits","sticky"))
    r.unpack("a","left");r.unpack("b","right");r.set("sign",0)
    r.if_(("ne","a_sign","b_sign"),lambda:r.set("sign",1))
    for n,other in (("a","b"),("b","a")):
        def special(n=n,other=other):
            r.nonzero(n,lambda:r.special("nan"));r.zero(other);nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{nonzero}");r.special("nan");r.mark(nonzero)
            if other=="b":r.if_(("eq","b_exp",255),lambda:r.nonzero("b",lambda:r.special("nan")))
            r.special("inf","sign")
        r.if_(("eq",n+"_exp",255),special)
    r.set("exponent",sub(add("a_exp","b_exp"),127));r.set_pair("x_low","a");r.set_pair("y","b")
    for n in ("x_high","p_low","p_high"):r.set(n+"_lo",0);r.set(n+"_hi",0)
    r.set("bits",24)
    def multiply_step():
        r.expr("y_lo");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}")
        r.binary("add","p_low","p_low","x_low");r.binary("add","p_high","p_high","x_high",carry=True);r.mark(skip)
        r.shift("x_low","left");r.registers("x_high");r.emit("rl l","rl h","rl e","rl d");r.save_registers("x_high")
        r.shift("y","right");r.set("bits",sub("bits",1))
    r.while_(("ne","bits",0),multiply_step);r.set("bits",20);r.set("sticky",0)
    def reduce_product():
        r.shift("p_high","right");r.registers("p_low");r.emit("rr d","rr e","rr h","rr l");r.save_registers("p_low")
        no_bit=r.label();r.emit(f"jp nc,{no_bit}");r.set("sticky",1);r.mark(no_bit);r.set("bits",sub("bits",1))
    r.while_(("ne","bits",0),reduce_product)
    def sticky():
        r.expr("p_low_lo");r.emit("set 0,l");o=r.slots["p_low_lo"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.if_(("ne","sticky",0),sticky);r.packed("p_low","exponent","sign");out.append(r.finish())
    r=FloatRoutine("divide",("left","right"),("a","b","m"),("a_exp","a_sign","b_exp","b_sign","sign","exponent","bits"))
    r.unpack("a","left");r.unpack("b","right");r.set("sign",0);r.if_(("ne","a_sign","b_sign"),lambda:r.set("sign",1))
    def a_special():
        r.nonzero("a",lambda:r.special("nan"));r.if_(("eq","b_exp",255),lambda:r.special("nan"));r.special("inf","sign")
    r.if_(("eq","a_exp",255),a_special)
    def b_special():r.nonzero("b",lambda:r.special("nan"));r.special("zero","sign")
    r.if_(("eq","b_exp",255),b_special)
    r.zero("b");not_zero=r.label();r.emit("ld a,h","or l",f"jp nz,{not_zero}");r.fail(8);r.mark(not_zero)
    r.zero("a");not_zero=r.label();r.emit("ld a,h","or l",f"jp nz,{not_zero}");r.special("zero","sign");r.mark(not_zero)
    r.normalize("a");r.normalize("b");r.set("exponent",add(sub("a_exp","b_exp"),127))
    r.set("m_lo",0);r.set("m_hi",0)
    r.ge("a","b",lambda:(r.binary("subtract","a","a","b"),r.set("m_lo",1)))
    r.set("bits",26)
    def divide_step():
        r.shift("a","left");r.shift("m","left")
        r.ge("a","b",lambda:(r.binary("subtract","a","a","b"),r.set("m_lo",add("m_lo",1))))
        r.set("bits",sub("bits",1))
    r.while_(("ne","bits",0),divide_step)
    def sticky():
        r.expr("m_lo");r.emit("set 0,l");o=r.slots["m_lo"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.nonzero("a",sticky);r.packed("m","exponent","sign");out.append(r.finish())
    for name in ("negate","abs"):
        r=FloatRoutine(name,("value",),("m",));r.box("m","value")
        r.expr("m_hi");r.emit("ld a,h","xor 128" if name=="negate" else "and 127","ld h,a")
        o=r.slots["m_hi"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h");r.bits("m_lo","m_hi");out.append(r.finish())
    r=FloatRoutine("integer",("value",),("m",),("m_exp","m_sign","count"));r.unpack("m","value")
    r.if_(("eq","m_exp",255),lambda:r.fail(14))
    r.if_(("sge","m_exp",159),lambda:r.fail(14))
    def left():
        r.set("count",sub("m_exp",150));r.if_(("uge","count",32),lambda:r.fail(14))
        r.while_(("ne","count",0),lambda:(r.shift("m","left"),r.set("count",sub("count",1))))
    def right():
        r.set("count",sub(150,"m_exp"));r.if_(("uge","count",32),lambda:r.set("count",32))
        r.while_(("ne","count",0),lambda:(r.shift("m","right"),r.set("count",sub("count",1))))
    r.if_(("sge","m_exp",150),left,right)
    r.if_(("uge","m_hi",32768),lambda:r.if_(("ne","m_hi",32768),lambda:r.fail(14)))
    def boundary():
        r.if_(("eq","m_sign",0),lambda:r.fail(14));r.if_(("ne","m_lo",0),lambda:r.fail(14))
    r.if_(("eq","m_hi",32768),boundary)
    r.if_(("ne","m_sign",0),lambda:r.negate("m"));r.result("m");out.append(r.finish())
    r=FloatRoutine("int",("value",),("m",),("object",))
    r.set("object",("call","__obj_f32_integer","value"));r.box("m","object");r.expr(("call","__arr_release","object"))
    def negative():r.if_(("ne","m_hi",65535),lambda:r.fail(14))
    def positive():r.if_(("ne","m_hi",0),lambda:r.fail(14))
    r.if_(("negative","m_lo"),negative,positive);r.ret("m_lo");out.append(r.finish())
    r=FloatRoutine("compare",("left","right"),("a","b"),("a_exp","a_sign","b_exp","b_sign"))
    r.unpack("a","left");r.unpack("b","right")
    for n in ("a","b"):r.if_(("eq",n+"_exp",255),lambda n=n:r.nonzero(n,lambda:r.ret(2)))
    for n,source in (("a","left"),("b","right")):
        r.box(n,source);r.expr(n+"_hi");r.emit("ld a,h","and 127","ld h,a");o=r.slots[n+"_hi"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    # Signed zeros compare equal; a nonzero magnitude sorts on its sign.
    r.zero("a");a_nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{a_nonzero}")
    r.zero("b");b_nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{b_nonzero}");r.ret(0);r.mark(b_nonzero);r.mark(a_nonzero)
    r.if_(("ne","a_sign","b_sign"),lambda:(r.if_(("ne","a_sign",0),lambda:r.ret(-1)),r.ret(1)))
    r.box("a","left");r.box("b","right")
    r.if_(("eq","a_hi","b_hi"),lambda:r.if_(("eq","a_lo","b_lo"),lambda:r.ret(0)))
    def greater():r.if_(("ne","a_sign",0),lambda:r.ret(-1));r.ret(1)
    r.ge("a","b",greater);r.if_(("ne","a_sign",0),lambda:r.ret(1));r.ret(-1);out.append(r.finish())
    r=Routine("round",("value","places","integer"),("text","rounded","result"),prefix="__obj_f32_")
    r.set("text",("call","__obj_f32_text","value","places",1));r.set("rounded",("call","__obj_f32_parse","text"));r.invoke("release","text")
    def integer():
        r.set("result",("call","__obj_f32_int","rounded"));r.invoke("release","rounded");r.ret("result")
    r.if_(("ne","integer",0),integer);r.ret("rounded");out.append(r.finish())
    r=Routine("power",("value","exponent"),("base","result","temporary","count","negative","one"),prefix="__obj_f32_")
    r.set("base",call("retain","value"));r.set("result",("call","__obj_f32_new",0,16256));r.set("negative",0);r.set("count","exponent")
    r.if_(("negative","count"),lambda:(r.set("negative",1),r.set("count",sub(0,"count"))))
    def exponent():
        r.expr("count");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}")
        r.set("temporary",("call","__obj_f32_multiply","result","base"));r.invoke("release","result");r.set("result","temporary");r.mark(skip)
        r.expr("count");r.emit("srl h","rr l");o=r.slots["count"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        def square():
            r.set("temporary",("call","__obj_f32_multiply","base","base"));r.invoke("release","base");r.set("base","temporary")
        r.if_(("ne","count",0),square)
    r.while_(("ne","count",0),exponent);r.invoke("release","base")
    def reciprocal():
        r.set("one",("call","__obj_f32_new",0,16256));r.set("temporary",("call","__obj_f32_divide","one","result"))
        r.invoke("release","one");r.invoke("release","result");r.set("result","temporary")
    r.if_(("ne","negative",0),reciprocal);r.ret("result");out.append(r.finish())
    return out
