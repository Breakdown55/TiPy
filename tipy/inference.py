"""Return-shape inference before call checking; definite assignment is separate."""
from .types import is_dict,dict_types,is_tuple,tuple_types,is_set,element
import re


def infer_returns(tree):
    functions={n["name"]:n for n in tree["body"] if n["kind"]=="function"}
    automatic={name for name,f in functions.items() if f["return_type"]=="auto"}
    globals_={}
    def norm(t):return re.sub(r"\bint\b","i16",t)
    def expr(n,scope):
        k=n["kind"]
        if k in {"number","hash","cast_int"}:return "i16"
        if k in {"boolean","compare","compare_chain","and","or","not","membership"}:return "bool"
        if k in {"string","fstring","format_value"}:return "str"
        if k=="decimal":return "fixed"
        if k=="conditional":return expr(n["body"],scope) or expr(n["alternative"],scope)
        if k=="name":return scope.get(n["name"],globals_.get(n["name"]))
        if k in {"add","subtract","multiply","floor_divide","modulo","negate"}:return expr(n.get("left",n.get("value")),scope)
        if k=="tuple":
            ts=[expr(v,scope) for v in n["values"]]
            return "tuple["+",".join(ts)+"]" if all(ts) else None
        if k=="list":
            t=expr(n["values"][0],scope) if n["values"] else "i16"
            return t+"[]" if t else None
        if k=="set":
            t=expr(n["values"][0],scope) if n["values"] else "i16"
            return f"set[{t}]" if t else None
        if k=="dict":
            if not n["entries"]:return "dict[i16,i16]"
            e=n["entries"][0];key,value=expr(e["key"],scope),expr(e["value"],scope)
            return f"dict[{key},{value}]" if key and value else None
        if k=="comprehension":
            local=scope.copy();scan([n["loop"]],local,[]);t=expr(n["value"],local)
            if n.get("collection")=="dict":
                key=expr(n["key"],local);return f"dict[{key},{t}]" if key and t else None
            if n.get("collection")=="set":return f"set[{t}]" if t else None
            return t+"[]" if t else None
        if k=="call":
            name=n["name"]
            if name in functions:
                t=functions[name]["return_type"];return norm(t) if t!="auto" else None
            if name in {"int","len","abs","sum","min","max","hash","ord"}:return "i16"
            if name in {"input","str","chr","repr"}:return "str"
            if name=="set":
                if not n["arguments"]:return "set[i16]"
                t=expr(n["arguments"][0],scope)
                return t if t and is_set(t) else f"set[{t[:-2]}]" if t and t.endswith("[]") else "set[str]" if t=="str" else None
            if name in {"range","list"}:return "i16[]" if not n["arguments"] or name=="range" else expr(n["arguments"][0],scope)
        if k=="method":
            t=expr(n["base"],scope)
            if n["name"]=="copy":return t
            if t and is_set(t):return t if n["name"] in {"union","intersection","difference","symmetric_difference"} else element(t) if n["name"]=="pop" else "void"
            if t=="str":return "str[]" if n["name"]=="split" else "str"
            if t and t.endswith("[]") and n["name"]=="pop":return t[:-2]
            if t and is_dict(t) and n["name"] in {"get","pop","setdefault"}:return dict_types(t)[1]
        if k=="index":
            t=expr(n["base"],scope)
            if t=="str":return "str"
            if t and t.endswith("[]"):return t[:-2]
            if t and is_dict(t):return dict_types(t)[1]
            if t and is_tuple(t):
                ts=tuple_types(t);i=n["index"]
                if i["kind"]=="number" and 0<=i["value"]<len(ts):return ts[i["value"]]
                if ts and len(set(ts))==1:return ts[0]
        return None
    def scan(body,scope,returns):
        for n in body:
            k=n["kind"]
            if k in {"declare","assign"}:
                t=expr(n["value"],scope);declared=norm(n.get("type","auto"))
                if declared!="auto" and not (t and t.endswith("[]")):t=declared
                if t:scope[n["name"]]=t
            elif k=="return":
                t=expr(n["value"],scope) if n["value"] else "void"
                if t:returns.append(t)
            elif k=="for":
                iterable=n["iterable"];t=expr(iterable,scope);elements=[]
                if iterable.get("name")=="range":elements=["i16"]
                elif iterable["kind"]=="method" and iterable["name"] in {"items","keys","values"}:
                    source=expr(iterable["base"],scope)
                    if source and is_dict(source):
                        key,value=dict_types(source);elements=[key,value] if iterable["name"]=="items" else [value] if iterable["name"]=="values" else [key]
                elif t and t.endswith("[]"):elements=[t[:-2]]
                elif t and is_dict(t):elements=[dict_types(t)[0]]
                for name,element in zip(n.get("names",[n["name"]]),elements):scope[name]=element
                scan(n["body"],scope,returns)
            elif k in {"if","while"}:
                scan(n["body"],scope,returns);scan(n.get("alternative",[]),scope,returns)
    for _ in range(max(2,len(functions)+1)):
        scan(tree["body"],globals_,[])
        for name in automatic:
            f=functions[name];scope={p["name"]:norm(p["type"]) for p in f["params"]};returns=[]
            scan(f["body"],scope,returns)
            if returns and len(set(returns))==1:f["return_type"]=returns[0]
