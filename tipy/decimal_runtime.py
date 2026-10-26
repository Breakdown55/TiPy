"""Exact little-endian decimal digits for native binary32 text conversion."""
from .list_runtime import Routine,add,sub,word,byte,call
from .float_runtime import FloatRoutine


def dc(name,*args):return ("call","__obj_decimal_"+name,*args)


def routines():
    out=[]
    r=Routine("trim",("digits",),("length",),prefix="__obj_decimal_")
    r.set("length",word(add("digits",2)));loop,end=r.label(),r.label();r.mark(loop)
    r.false_jump(("uge","length",2),end);r.false_jump(("eq",byte(add(word("digits"),sub("length",1))),0),end)
    r.set("length",sub("length",1));r.emit(f"jp {loop}");r.mark(end);r.write(add("digits",2),"length");r.ret();out.append(r.finish())
    r=Routine("multiply",("digits","factor"),("index","carry","value","original"),prefix="__obj_decimal_")
    r.set("index",0);r.set("carry",0)
    def digit():
        r.set("original",byte(add(word("digits"),"index")));r.set("value",add("original","original"))
        r.if_(("eq","factor",5),lambda:r.set("value",add(add("value","value"),"original")))
        r.set("value",add("value","carry"));r.set("carry",0)
        r.while_(("uge","value",10),lambda:(r.set("value",sub("value",10)),r.set("carry",add("carry",1))))
        r.write(add(word("digits"),"index"),"value",1);r.set("index",add("index",1))
    r.while_(("ult","index",word(add("digits",2))),digit);r.if_(("ne","carry",0),lambda:r.invoke("append","digits","carry"));r.ret();out.append(r.finish())
    r=Routine("half",("digits",),("index","carry","value","next"),prefix="__obj_decimal_")
    r.set("index",word(add("digits",2)));r.set("carry",0)
    def digit():
        r.set("index",sub("index",1));r.set("value",byte(add(word("digits"),"index")))
        r.if_(("ne","carry",0),lambda:r.set("value",add("value",10)))
        r.expr("value");r.emit("ld a,l","and 1","ld l,a","ld h,0");o=r.slots["carry"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        r.expr("value");r.emit("srl h","rr l");o=r.slots["value"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
        r.write(add(word("digits"),"index"),"value",1)
    r.while_(("ne","index",0),digit);r.expr(dc("trim","digits"));r.ret("carry");out.append(r.finish())
    r=Routine("at",("digits","index"),prefix="__obj_decimal_")
    r.if_(("uge","index",word(add("digits",2))),lambda:r.ret(0));r.ret(byte(add(word("digits"),"index")));out.append(r.finish())
    r=FloatRoutine("parse",("text",),("m",),("source","digits","index","length","character","negative","dot","seen","scale","exponent","exponent_sign","exponent_digits","point","bits","remainder"))
    r.set("source",("call","__str_strip","text",0,0,1));r.set("length",word(add("source",2)))
    r.set("index",0);r.set("negative",0);r.set("dot",0);r.set("seen",0);r.set("scale",0);r.set("exponent",0);r.set("exponent_sign",0);r.set("exponent_digits",0)
    def character():r.set("character",byte(add(word("source"),"index")))
    r.if_(("eq","length",0),lambda:r.fail(14));character()
    r.if_(("eq","character",45),lambda:(r.set("negative",1),r.set("index",1)))
    r.if_(("eq","character",43),lambda:r.set("index",1))
    r.if_(("uge","index","length"),lambda:r.fail(14));character()
    def special(chars,kind):
        for offset,c in enumerate(chars):
            r.if_(("uge",add("index",offset),"length"),lambda:r.fail(14))
            r.expr(byte(add(word("source"),add("index",offset))));r.emit("ld a,l","or 32","ld l,a","ld h,0")
            r.emit(f"ld de,{ord(c)}","or a","sbc hl,de");valid=r.label();r.emit(f"jp z,{valid}");r.fail(14);r.mark(valid)
        r.if_(("ne",add("index",len(chars)),"length"),lambda:r.fail(14))
        r.invoke("release","source");r.special(kind,"negative")
    r.if_(("eq","character",105),lambda:special("inf","inf"));r.if_(("eq","character",73),lambda:special("inf","inf"))
    r.if_(("eq","character",110),lambda:special("nan","nan"));r.if_(("eq","character",78),lambda:special("nan","nan"))
    r.set("digits",call("new",1));mantissa,end_mantissa=r.label(),r.label();r.mark(mantissa)
    r.false_jump(("ult","index","length"),end_mantissa);character()
    def digit():
        r.invoke("append","digits",sub("character",48));r.set("seen",1)
        r.if_(("ne","dot",0),lambda:r.set("scale",add("scale",1)))
        r.set("index",add("index",1));r.emit(f"jp {mantissa}")
    is_digit,not_digit=r.label(),r.label();r.false_jump(("uge","character",48),not_digit);r.false_jump(("ult","character",58),not_digit);digit();r.mark(not_digit)
    def dot():
        r.if_(("ne","dot",0),lambda:r.fail(14));r.set("dot",1);r.set("index",add("index",1));r.emit(f"jp {mantissa}")
    r.if_(("eq","character",46),dot);r.mark(end_mantissa)
    r.if_(("eq","seen",0),lambda:r.fail(14))
    def exponent():
        character();allowed=r.label()
        r.if_(("eq","character",101),lambda:r.emit(f"jp {allowed}"));r.if_(("eq","character",69),lambda:r.emit(f"jp {allowed}"));r.fail(14);r.mark(allowed)
        r.set("index",add("index",1));r.if_(("uge","index","length"),lambda:r.fail(14));character()
        r.if_(("eq","character",45),lambda:(r.set("exponent_sign",1),r.set("index",add("index",1))))
        r.if_(("eq","character",43),lambda:r.set("index",add("index",1)))
        def digit():
            character();r.if_(("ult","character",48),lambda:r.fail(14));r.if_(("uge","character",58),lambda:r.fail(14))
            def accumulate():
                r.expr("exponent");r.emit("add hl,hl","push hl","add hl,hl","add hl,hl","pop de","add hl,de")
                o=r.slots["exponent"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h");r.set("exponent",add("exponent",sub("character",48)))
            r.if_(("ult","exponent",4096),accumulate)
            r.if_(("uge","exponent",4096),lambda:r.set("exponent",4096))
            r.set("exponent_digits",1);r.set("index",add("index",1))
        r.while_(("ult","index","length"),digit);r.if_(("eq","exponent_digits",0),lambda:r.fail(14))
        r.if_(("ne","exponent_sign",0),lambda:r.set("exponent",sub(0,"exponent")))
    r.if_(("ult","index","length"),exponent)
    r.set("scale",sub("scale","exponent"));r.invoke("reverse","digits");r.expr(dc("trim","digits"))
    r.set("point",sub(word(add("digits",2)),"scale"))
    def zero():
        r.invoke("release","digits");r.invoke("release","source");r.special("zero","negative")
    def infinity():
        r.invoke("release","digits");r.invoke("release","source");r.special("inf","negative")
    r.if_(("sge","point",40),infinity);r.if_(("slt","point",-45),zero)
    r.if_(("eq",word(add("digits",2)),1),lambda:r.if_(("eq",byte(word("digits")),0),zero))
    r.while_(("negative","scale"),lambda:(r.invoke("insert","digits",0,0),r.set("scale",add("scale",1))))
    r.set("exponent",127);normalize,normalized=r.label(),r.label();r.mark(normalize)
    r.set("point",sub(word(add("digits",2)),"scale"))
    less,divide=r.label(),r.label();r.false_jump(("sge","point",1),less)
    r.false_jump(("slt","point",2),divide)
    r.false_jump(("ult",byte(add(word("digits"),sub(word(add("digits",2)),1))),2),divide)
    r.emit(f"jp {normalized}");r.mark(divide)
    r.set("remainder",dc("half","digits"))
    r.if_(("ne","remainder",0),lambda:(r.invoke("insert","digits",0,5),r.set("scale",add("scale",1))))
    r.set("exponent",add("exponent",1));r.emit(f"jp {normalize}");r.mark(less)
    r.expr(dc("multiply","digits",2));r.set("exponent",sub("exponent",1));r.emit(f"jp {normalize}");r.mark(normalized)
    r.write(add(word("digits"),"scale"),0,1);r.expr(dc("trim","digits"));r.set("m_lo",1);r.set("m_hi",0);r.set("bits",26)
    def binary_digit():
        r.shift("m","left");r.expr(dc("multiply","digits",2))
        def one():
            r.write(add(word("digits"),"scale"),0,1);r.expr(dc("trim","digits"));r.set("m_lo",add("m_lo",1))
        r.if_(("ne",dc("at","digits","scale"),0),one);r.set("bits",sub("bits",1))
    r.while_(("ne","bits",0),binary_digit)
    r.set("remainder",word(add("digits",2)))
    def sticky():
        r.expr("m_lo");r.emit("set 0,l");o=r.slots["m_lo"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
    r.if_(("uge","remainder",2),sticky,lambda:r.if_(("ne",byte(word("digits")),0),sticky))
    r.invoke("release","digits");r.invoke("release","source");r.packed("m","exponent","negative");out.append(r.finish())
    return out
