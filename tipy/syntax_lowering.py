"""Lower definition-time defaults and loop completion without changing scope."""
from copy import deepcopy
from .errors import CompileError


def prepare(tree):
    serial=0
    def location(n):return {k:n[k] for k in ("line","column","filename") if k in n}
    def body(items,break_flag=None):
        nonlocal serial
        result=[]
        for n in items:
            k=n["kind"];loc=location(n)
            if k=="function":
                seen_default=False;seen_var=False
                for p in n["params"]:
                    if seen_var:raise CompileError("The variadic parameter must be last",p)
                    if p.get("variadic"):
                        if p.get("default") is not None:raise CompileError("A variadic parameter cannot have a default",p)
                        p["type"]="tuple_var["+p["type"]+"]";seen_var=True
                    elif p.get("default") is not None:
                        serial+=1;name=f"$default_{serial}"
                        result.append({"kind":"declare","name":name,"type":p["type"],"value":p.pop("default"),**loc})
                        p["default_ref"]={"kind":"name","name":name,**loc};seen_default=True
                    elif p.get("default_capture"):seen_default=True
                    elif seen_default:raise CompileError("A required parameter cannot follow a default parameter",p)
                n["body"]=body(n["body"])
            elif k in {"for","while"}:
                alternative=n.pop("alternative",[])
                flag=None
                if alternative:
                    serial+=1;flag=f"$completed_{serial}"
                    result.append({"kind":"assign","name":flag,"inferred":True,"value":{"kind":"boolean","value":True,**loc},**loc})
                n["body"]=body(n["body"],flag)
                result.append(n)
                if flag:
                    result.append({"kind":"if","condition":{"kind":"name","name":flag,**loc},"body":body(alternative,break_flag),"alternative":[],**loc})
                continue
            elif k=="if":
                n["body"]=body(n["body"],break_flag);n["alternative"]=body(n["alternative"],break_flag)
            elif k in {"try_except","try_finally"}:
                n["body"]=body(n["body"],break_flag)
                for handler in n.get("handlers",[]):handler["body"]=body(handler["body"],break_flag)
                for key in ("alternative","final"):
                    if key in n:n[key]=body(n[key],break_flag)
            elif k=="break" and break_flag:
                result.append({"kind":"assign","name":break_flag,"inferred":False,"value":{"kind":"boolean","value":False,**loc},**loc})
            result.append(n)
        return result
    tree["body"]=body(tree["body"])
