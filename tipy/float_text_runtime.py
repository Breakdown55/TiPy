"""Decimal formatting from exact binary32 significands, entirely in Z80."""
from .float_runtime import FloatRoutine
from .list_runtime import Routine,add,sub,word,byte,call


def routines():
    out=[]
    r=FloatRoutine("text",("object","precision","mode"),("m",),("m_exp","m_sign","digits","result","integer","scale","count","factor","index","value","carry","length","start","cut","sticky","guard","parity","rounded","exponent","tens","position","places"))
    r.unpack("m","object")
    r.if_(("slt","precision",-50),lambda:r.set("precision",-50));r.if_(("sge","precision",51),lambda:r.set("precision",50))
    def literal(name,chars):r.ret(call("from_data",1,len(chars),"@__f32_text_"+name))
    def special():
        r.nonzero("m",lambda:literal("nan","nan"))
        r.if_(("ne","m_sign",0),lambda:literal("negative_inf","-inf"));literal("inf","inf")
    r.if_(("eq","m_exp",255),special)
    r.set("integer",("call","__obj_i32_new","m_lo","m_hi"));r.set("digits",("call","__obj_i32_string","integer"));r.invoke("release","integer")
    r.invoke("reverse","digits");r.set("index",0)
    def numeric_digit():
        r.write(add(word("digits"),"index"),sub(byte(add(word("digits"),"index")),48),1);r.set("index",add("index",1))
    r.while_(("ult","index",word(add("digits",2))),numeric_digit)
    r.set("count",sub("m_exp",150));r.set("scale",0);r.set("factor",2)
    r.if_(("negative","count"),lambda:(r.set("count",sub(0,"count")),r.set("scale","count"),r.set("factor",5)))
    r.zero("m");not_zero=r.label();r.emit("ld a,h","or l",f"jp nz,{not_zero}");r.set("count",0);r.set("scale",0);r.mark(not_zero)
    def multiply():
        r.set("index",0);r.set("carry",0)
        def digit():
            r.set("value",byte(add(word("digits"),"index")))
            r.set("value",add("value","value"))
            r.if_(("eq","factor",5),lambda:r.set("value",add(add("value","value"),byte(add(word("digits"),"index")))))
            r.set("value",add("value","carry"));r.set("carry",0)
            r.while_(("uge","value",10),lambda:(r.set("value",sub("value",10)),r.set("carry",add("carry",1))))
            r.write(add(word("digits"),"index"),"value",1);r.set("index",add("index",1))
        r.while_(("ult","index",word(add("digits",2))),digit)
        r.if_(("ne","carry",0),lambda:r.invoke("append","digits","carry"));r.set("count",sub("count",1))
    r.while_(("ne","count",0),multiply)
    r.set("length",word(add("digits",2)));r.set("cut",sub("length",7))
    r.if_(("ne","mode",0),lambda:r.set("cut",sub("scale","precision")))
    r.if_(("negative","cut"),lambda:r.set("cut",0))
    def round_digits():
        r.set("guard",0);r.set("sticky",0);r.set("parity",0);r.set("rounded",0)
        def within():
            r.set("guard",byte(add(word("digits"),sub("cut",1))));r.set("index",0)
            def sticky():
                r.if_(("ne",byte(add(word("digits"),"index")),0),lambda:r.set("sticky",1));r.set("index",add("index",1))
            r.while_(("ult","index",sub("cut",1)),sticky)
            def parity():
                r.expr(byte(add(word("digits"),"cut")));r.emit("ld a,l","and 1","ld l,a","ld h,0");o=r.slots["parity"];r.emit(f"ld (ix{o:+d}),l",f"ld (ix{o+1:+d}),h")
            r.if_(("ult","cut","length"),parity)
        r.if_(("uge","length","cut"),within)
        r.if_(("uge","guard",6),lambda:r.set("rounded",1))
        r.if_(("eq","guard",5),lambda:r.if_(("ne",add("sticky","parity"),0),lambda:r.set("rounded",1)))
        def increment():
            r.set("index","cut");r.set("carry",1)
            def digit():
                def existing():
                    r.set("value",add(byte(add(word("digits"),"index")),1));r.set("carry",0)
                    r.if_(("eq","value",10),lambda:(r.set("value",0),r.set("carry",1)))
                    r.write(add(word("digits"),"index"),"value",1)
                r.if_(("ult","index",word(add("digits",2))),existing,lambda:(r.invoke("append","digits",1),r.set("carry",0)))
                r.set("index",add("index",1))
            r.while_(("ne","carry",0),digit)
        r.if_(("ne","rounded",0),increment)
        def zero():
            r.write(word("digits"),0,1);r.write(add("digits",2),1);r.set("scale",0);r.set("cut",0)
        r.if_(("uge","cut",word(add("digits",2))),zero)
    r.if_(("ne","cut",0),round_digits)
    r.set("length",word(add("digits",2)));r.set("start","cut")
    def trim():
        begin,end=r.label(),r.label();r.mark(begin);r.false_jump(("ult","start",sub("length",1)),end)
        r.false_jump(("eq",byte(add(word("digits"),"start")),0),end);r.set("start",add("start",1));r.emit(f"jp {begin}");r.mark(end)
    r.if_(("eq","mode",0),trim)
    r.set("result",call("new",1));r.if_(("ne","m_sign",0),lambda:r.invoke("append","result",45))
    r.set("exponent",sub(sub("length","scale"),1))
    def append_position():
        r.set("value",0)
        def within():r.if_(("uge","position","cut"),lambda:r.set("value",byte(add(word("digits"),"position"))))
        r.if_(("ult","position","length"),within);r.invoke("append","result",add("value",48))
    def scientific():
        r.set("position",sub("length",1));append_position();r.set("position",sub("position",1))
        def fraction():
            r.invoke("append","result",46)
            r.while_(("sge","position","start"),lambda:(append_position(),r.set("position",sub("position",1))))
        r.if_(("sge","position","start"),fraction);r.invoke("append","result",101)
        r.if_(("negative","exponent"),lambda:(r.invoke("append","result",45),r.set("exponent",sub(0,"exponent"))),lambda:r.invoke("append","result",43))
        r.set("tens",0);r.while_(("uge","exponent",10),lambda:(r.set("exponent",sub("exponent",10)),r.set("tens",add("tens",1))))
        r.invoke("append","result",add("tens",48));r.invoke("append","result",add("exponent",48))
    def fixed():
        r.set("position",sub("length",1))
        r.if_(("slt","position","scale"),lambda:r.invoke("append","result",48),lambda:r.while_(("sge","position","scale"),lambda:(append_position(),r.set("position",sub("position",1)))))
        r.set("places",sub("scale","start"))
        r.if_(("ne","mode",0),lambda:r.set("places","precision"))
        if_positive=r.label();r.false_jump(("sge","places",1),if_positive)
        r.invoke("append","result",46);r.set("position",sub("scale",1))
        r.while_(("sge","places",1),lambda:(append_position(),r.set("position",sub("position",1)),r.set("places",sub("places",1))))
        after=r.label();r.emit(f"jp {after}");r.mark(if_positive)
        r.if_(("eq","mode",0),lambda:(r.invoke("append","result",46),r.invoke("append","result",48)));r.mark(after)
    if_general=r.label();done=r.label();r.false_jump(("eq","mode",0),if_general)
    sci=r.label();r.false_jump(("slt","exponent",7),sci);r.false_jump(("sge","exponent",-4),sci)
    fixed();r.emit(f"jp {done}");r.mark(sci);scientific();r.emit(f"jp {done}");r.mark(if_general);fixed();r.mark(done)
    r.invoke("release","digits");r.ret("result")
    out.append(r.finish()+"\n__f32_text_nan:\n        db 110,97,110\n__f32_text_inf:\n        db 105,110,102\n__f32_text_negative_inf:\n        db 45,105,110,102")
    return out
