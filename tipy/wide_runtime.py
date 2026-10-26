"""Native signed 32-bit integers, boxed behind the ordinary word-sized ABI."""
from .list_runtime import Routine,add,sub,word,call


class WideRoutine(Routine):
    def __init__(self,name,params=(),pairs=(),locals_=(),prefix="__obj_i32_"):
        super().__init__(name,params,tuple(v+part for v in pairs for part in ("_lo","_hi"))+tuple(locals_),prefix=prefix)
    def box(self,name,value):
        self.set(name+"_lo",word(word(value)));self.set(name+"_hi",word(add(word(value),2)))
    def set_pair(self,target,source):
        for part in ("_lo","_hi"):self.set(target+part,source+part)
    def registers(self,name):
        self.expr(name+"_lo");self.emit("push hl");self.expr(name+"_hi");self.emit("ex de,hl","pop hl")
    def save_registers(self,name):
        o=self.slots[name+"_lo"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h","ex de,hl")
        o=self.slots[name+"_hi"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    def binary(self,op,target,a,b,carry=False):
        for i,part in enumerate(("_lo","_hi")):
            self.expr(a+part);self.emit("push hl");self.expr(b+part);self.emit("ex de,hl","pop hl")
            if op in {"add","subtract"}:
                if not i and op=="subtract":self.emit("or a")
                self.emit(("adc hl,de" if i or carry else "add hl,de") if op=="add" else "sbc hl,de")
            else:
                opcode={"bit_and":"and","bit_or":"or","bit_xor":"xor"}[op]
                self.emit("ld a,l",f"{opcode} e","ld l,a","ld a,h",f"{opcode} d","ld h,a")
            o=self.slots[target+part];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    def negate(self,name):
        self.registers(name)
        self.emit("ld a,l","cpl","ld l,a","ld a,h","cpl","ld h,a","ld a,e","cpl","ld e,a","ld a,d","cpl","ld d,a","inc hl","ld a,h","or l")
        skip=self.label();self.emit(f"jp nz,{skip}","inc de");self.mark(skip);self.save_registers(name)
    def shift(self,name,kind):
        self.registers(name)
        if kind=="left":self.emit("sla l","rl h","rl e","rl d")
        else:self.emit("sra d" if kind=="signed_right" else "srl d","rr e","rr h","rr l")
        self.save_registers(name)
    def ge(self,a,b,yes):
        high_equal,done=self.label(),self.label()
        self.expr(a+"_hi");self.emit("push hl");self.expr(b+"_hi");self.emit("ex de,hl","pop hl","or a","sbc hl,de",f"jp z,{high_equal}",f"jp c,{done}")
        yes();self.emit(f"jp {done}");self.mark(high_equal)
        self.if_(("uge",a+"_lo",b+"_lo"),yes);self.mark(done)
    def zero(self,name):
        self.expr(name+"_lo");self.emit("push hl");self.expr(name+"_hi");self.emit("pop de","ld a,l","or e","ld l,a","ld a,h","or d","ld h,a")
    def multiply(self,target,a,b):
        self.set_pair("mul_a",a);self.set_pair("mul_b",b)
        self.set(target+"_lo",0);self.set(target+"_hi",0);self.set("mul_bits",32)
        def step():
            self.expr("mul_b_lo");self.emit("bit 0,l");skip=self.label();self.emit(f"jp z,{skip}")
            self.binary("add",target,target,"mul_a");self.mark(skip);self.shift("mul_a","left");self.shift("mul_b","right")
            self.set("mul_bits",("-","mul_bits",1))
        self.while_(("ne","mul_bits",0),step)
    def result(self,name):self.ret(("call","__obj_i32_new",name+"_lo",name+"_hi"))
    def divide_unsigned(self,n,d,remainder):
        self.set(remainder+"_lo",0);self.set(remainder+"_hi",0);self.set("bits",32)
        def step():
            self.shift(n,"left");self.registers(remainder);self.emit("rl l","rl h","rl e","rl d");self.save_registers(remainder)
            overflow,done=self.label(),self.label();self.emit(f"jp c,{overflow}")
            def subtract():
                self.binary("subtract",remainder,remainder,d);self.expr(n+"_lo");self.emit("set 0,l");o=self.slots[n+"_lo"];self.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
            self.ge(remainder,d,subtract);self.emit(f"jp {done}");self.mark(overflow);subtract();self.mark(done)
            self.set("bits",("-","bits",1))
        self.while_(("ne","bits",0),step)


def routines():
    out=[]
    r=WideRoutine("fixed_divide",("left","right","shift"),("n","d","rem"),("bits","sign"))
    for p,v in (("left","n"),("right","d")):
        r.set(v+"_lo",p);r.set(v+"_hi",0);r.if_(("negative",p),lambda v=v:r.set(v+"_hi",65535))
    r.if_(("eq","right",0),lambda:r.fail(8));r.set("sign",0)
    r.if_(("negative","left"),lambda:(r.set("sign",1),r.negate("n")))
    r.if_(("negative","right"),lambda:(r.set("sign",("-",1,"sign")),r.negate("d")))
    r.while_(("ne","shift",0),lambda:(r.shift("n","left"),r.set("shift",("-","shift",1))))
    r.divide_unsigned("n","d","rem")
    def negative():
        r.negate("n");r.zero("rem");end=r.label();r.emit("ld a,h","or l",f"jp z,{end}")
        r.expr("n_lo");r.emit("ld a,h","or l");no_borrow=r.label();r.emit(f"jp nz,{no_borrow}");r.set("n_hi",("-","n_hi",1));r.mark(no_borrow);r.set("n_lo",("-","n_lo",1));r.mark(end)
    r.if_(("ne","sign",0),negative)
    def positive():r.if_(("ne","n_hi",0),lambda:r.fail(14));r.if_(("negative","n_lo"),lambda:r.fail(14))
    def negative_range():r.if_(("ne","n_hi",65535),lambda:r.fail(14));r.if_(("sge","n_lo",0),lambda:r.fail(14))
    r.if_(("negative","n_hi"),negative_range,positive);r.ret("n_lo");out.append(r.finish())
    r=Routine("i32_new",("low","high"),("result",),prefix="__obj_")
    r.set("result",call("new",2));r.invoke("append","result","low");r.invoke("append","result","high");r.write(add("result",6),32763);r.ret("result");out.append(r.finish())
    r=Routine("i32_from_int",("value",),("high",),prefix="__obj_")
    r.set("high",0);r.if_(("negative","value"),lambda:r.set("high",65535))
    r.ret(("call","__obj_i32_new","value","high"));out.append(r.finish())
    for op in ("add","subtract","bit_and","bit_or","bit_xor"):
        r=WideRoutine(op,("left","right"),("a","b","result"));r.box("a","left");r.box("b","right")
        r.binary(op,"result","a","b");r.result("result");out.append(r.finish())
    for op in ("negate","invert","abs"):
        r=WideRoutine(op,("value",),("a",));r.box("a","value")
        if op=="negate":r.negate("a")
        elif op=="abs":r.if_(("negative","a_hi"),lambda:r.negate("a"))
        else:
            for part in ("lo","hi"):
                r.expr("a_"+part);r.emit("ld a,l","cpl","ld l,a","ld a,h","cpl","ld h,a")
                o=r.slots["a_"+part];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        r.result("a");out.append(r.finish())
    for op in ("shift_left","shift_right"):
        r=WideRoutine(op,("value","count"),("a",));r.box("a","value")
        r.if_(("negative","count"),lambda:r.fail(14));r.if_(("uge","count",32),lambda:r.set("count",32))
        def step():r.shift("a","left" if op=="shift_left" else "signed_right");r.set("count",("-","count",1))
        r.while_(("ne","count",0),step);r.result("a");out.append(r.finish())
    r=WideRoutine("compare",("left","right"),("a","b"));r.box("a","left");r.box("b","right")
    r.if_(("slt","a_hi","b_hi"),lambda:r.ret(-1));r.if_(("ne","a_hi","b_hi"),lambda:r.ret(1))
    r.if_(("ult","a_lo","b_lo"),lambda:r.ret(-1));r.if_(("ne","a_lo","b_lo"),lambda:r.ret(1));r.ret(0);out.append(r.finish())
    r=WideRoutine("multiply",("left","right"),("a","b","result","mul_a","mul_b"),("mul_bits",))
    r.box("a","left");r.box("b","right");r.multiply("result","a","b");r.result("result");out.append(r.finish())
    r=WideRoutine("power",("value","exponent"),("base","result","temporary","mul_a","mul_b"),("mul_bits",))
    r.box("base","value");r.set("result_lo",1);r.set("result_hi",0)
    r.if_(("negative","exponent"),lambda:r.fail(14))
    def exponent():
        r.expr("exponent");r.emit("bit 0,l");skip=r.label();r.emit(f"jp z,{skip}")
        r.multiply("temporary","result","base");r.set_pair("result","temporary");r.mark(skip)
        r.multiply("temporary","base","base");r.set_pair("base","temporary")
        r.expr("exponent");r.emit("srl h","rr l");o=r.slots["exponent"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.while_(("ne","exponent",0),exponent);r.result("result");out.append(r.finish())
    r=WideRoutine("divide",("left","right","mode"),("n","d","rem"),("bits","n_negative","d_negative","sign"))
    r.box("n","left");r.box("d","right");r.zero("d");nonzero=r.label();r.emit("ld a,h","or l",f"jp nz,{nonzero}");r.fail(8);r.mark(nonzero)
    r.set("n_negative",0);r.set("d_negative",0);r.set("sign",0)
    r.if_(("negative","n_hi"),lambda:(r.set("n_negative",1),r.set("sign",1),r.negate("n")))
    r.if_(("negative","d_hi"),lambda:(r.set("d_negative",1),r.set("sign",("-",1,"sign")),r.negate("d")))
    r.divide_unsigned("n","d","rem")
    def negative_result():
        r.negate("n");r.zero("rem");zero=r.label();r.emit("ld a,h","or l",f"jp z,{zero}")
        # Decrement the signed quotient and reflect the remainder around |d|.
        r.expr("n_lo");r.emit("ld a,h","or l");low_nonzero=r.label();r.emit(f"jp nz,{low_nonzero}");r.set("n_hi",("-","n_hi",1));r.mark(low_nonzero)
        r.set("n_lo",("-","n_lo",1));r.binary("subtract","rem","d","rem");r.mark(zero)
    r.if_(("ne","sign",0),negative_result)
    r.if_(("ne","d_negative",0),lambda:r.negate("rem"))
    r.if_(("eq","mode",0),lambda:r.result("n"));r.result("rem");out.append(r.finish())
    r=WideRoutine("string",("value",),("n","d","rem"),("bits","negative","result"));r.box("n","value")
    r.set("negative",0);r.if_(("negative","n_hi"),lambda:(r.set("negative",1),r.negate("n")))
    r.set("result",call("new",1));r.set("d_lo",10);r.set("d_hi",0)
    loop,done=r.label(),r.label();r.mark(loop);r.divide_unsigned("n","d","rem")
    r.invoke("append","result",add("rem_lo",48));r.zero("n");r.emit("ld a,h","or l",f"jp nz,{loop}")
    r.if_(("ne","negative",0),lambda:r.invoke("append","result",45));r.invoke("reverse","result");r.ret("result");out.append(r.finish())
    r=WideRoutine("radix",("value","shift","uppercase","prefix"),("n",),("negative","result","digit","bits"));r.box("n","value")
    r.set("negative",0);r.if_(("negative","n_hi"),lambda:(r.set("negative",1),r.negate("n")))
    r.set("result",call("new",1));loop=r.label();r.mark(loop);r.expr("n_lo")
    one,three,masked=r.label(),r.label(),r.label()
    r.emit("push hl");r.expr("shift");r.emit("ld a,l","cp 1",f"jp z,{one}","cp 3",f"jp z,{three}","pop hl","ld a,l","and 15",f"jp {masked}")
    r.mark(one);r.emit("pop hl","ld a,l","and 1",f"jp {masked}");r.mark(three);r.emit("pop hl","ld a,l","and 7");r.mark(masked);r.emit("ld l,a","ld h,0")
    o=r.slots["digit"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.if_(("uge","digit",10),lambda:r.set("digit",add("digit",7)))
    r.if_(("eq","uppercase",0),lambda:r.if_(("uge","digit",17),lambda:r.set("digit",add("digit",32))))
    r.invoke("append","result",add("digit",48));r.set("bits","shift")
    r.while_(("ne","bits",0),lambda:(r.shift("n","right"),r.set("bits",("-","bits",1))))
    r.zero("n");r.emit("ld a,h","or l",f"jp nz,{loop}")
    def prefix():
        r.set("digit",120);r.if_(("ne","uppercase",0),lambda:r.set("digit",88))
        r.if_(("eq","shift",1),lambda:r.set("digit",98));r.if_(("eq","shift",3),lambda:r.set("digit",111))
        r.invoke("append","result","digit");r.invoke("append","result",48)
    r.if_(("ne","prefix",0),prefix);r.if_(("ne","negative",0),lambda:r.invoke("append","result",45))
    r.invoke("reverse","result");r.ret("result");out.append(r.finish())
    r=WideRoutine("round",("value","places"),("n","d","rem","temporary","mul_a","mul_b"),("bits","negative","count","roundup","mul_bits"))
    r.if_(("sge","places",0),lambda:r.ret(call("retain","value")))
    r.box("n","value");r.set("negative",0)
    r.if_(("negative","n_hi"),lambda:(r.set("negative",1),r.negate("n")))
    r.if_(("slt","places",-9),lambda:r.ret(("call","__obj_i32_new",0,0)))
    r.set("count",sub(0,"places"));r.set("d_lo",1);r.set("d_hi",0)
    def power10():
        r.set_pair("temporary","d");r.shift("temporary","left")
        for _ in range(3):r.shift("d","left")
        r.binary("add","d","d","temporary");r.set("count",sub("count",1))
    r.while_(("ne","count",0),power10);r.divide_unsigned("n","d","rem");r.shift("rem","left");r.set("roundup",0)
    def greater():
        r.set("roundup",1)
        def tie():
            r.expr("n_lo");r.emit("bit 0,l");odd=r.label();r.emit(f"jp nz,{odd}");r.set("roundup",0);r.mark(odd)
        r.if_(("eq","rem_hi","d_hi"),lambda:r.if_(("eq","rem_lo","d_lo"),tie))
    r.ge("rem","d",greater)
    def increment():
        r.registers("n");r.emit("inc hl","ld a,h","or l");no_carry=r.label();r.emit(f"jp nz,{no_carry}","inc de");r.mark(no_carry);r.save_registers("n")
    r.if_(("ne","roundup",0),increment);r.multiply("temporary","n","d")
    r.if_(("ne","negative",0),lambda:r.negate("temporary"));r.result("temporary");out.append(r.finish())
    r=Routine("round_int",("value","places"),("object","result"),prefix="__obj_i32_")
    r.set("object",("call","__obj_i32_round","value","places"));r.set("result",("call","__obj_read","object",0));r.invoke("release","object");r.ret("result");out.append(r.finish())
    return out
