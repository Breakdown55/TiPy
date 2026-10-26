"""Return-shape inference before call checking; definite assignment is separate."""
from .types import is_dict,dict_types,is_tuple,tuple_types,is_set,element,iterable_element,is_iterator,is_object
import re


def infer_returns(tree):
    functions={n["name"]:n for n in tree["body"] if n["kind"]=="function"}
    automatic={name for name,f in functions.items() if f["return_type"]=="auto"}
    globals_={}
    classes=tree.get("classes",{})
    def norm(t):return re.sub(r"\bint\b","i16",t)
    def merge(a,b):
        if not a:return b
        if not b or a==b:return a
        if a=="none":return b if b.startswith("optional[") else f"optional[{b}]"
        if b=="none":return a if a.startswith("optional[") else f"optional[{a}]"
        if a==f"optional[{b}]":return a
        if b==f"optional[{a}]":return b
        return None
    def expr(n,scope):
        k=n["kind"]
        if k=="none":return "none"
        if k=="identity":return "bool"
        if k in {"number","hash","cast_int"}:return "i16"
        if k in {"boolean","compare","compare_chain","and","or","not","membership"}:return "bool"
        if k in {"string","fstring","format_value"}:return "str"
        if k=="decimal":
            from decimal import Decimal
            return "fixed" if Decimal('-128')<=Decimal(n['value'])<=Decimal('127.99609375') else "float"
        if k=="true_divide":return "float"
        if k=="conditional":return merge(expr(n["body"],scope),expr(n["alternative"],scope))
        if k=="name":return scope.get(n["name"],globals_.get(n["name"]))
        if k=="closure":
            functions[n["function"]]["capture_hints"]={v["name"]:scope.get(v["name"],globals_.get(v["name"])) for v in n["captures"]}
            return f"closure[{n['function']}]"
        if k in {"add","subtract","multiply","floor_divide","modulo","negate"}:
            t=expr(n.get("left",n.get("value")),scope)
            other=expr(n["right"],scope) if "right" in n else None
            if "float" in {t,other}:return "float"
            if "i32" in {t,other}:return "i32"
            return t[9:-1] if t and t.startswith("optional[") else t
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
            if name in classes:return f"object[{name}]"
            if name=="i32":return "i32"
            if name=="float":return "float"
            if name in functions:
                f=functions[name]
                if f.get("generator"):
                    t=f.get("yield_type","auto");return f"iter[{norm(t)},generator,{name}]" if t!="auto" else None
                t=f["return_type"];return norm(t) if t!="auto" else None
            if name in {"int","len","abs","sum","min","max","hash","ord","round","pow"}:return "i16"
            if name=="divmod":return "tuple[i16,i16]"
            if name in {"input","str","chr","repr"}:return "str"
            if name=="set":
                if not n["arguments"]:return "set[i16]"
                t=expr(n["arguments"][0],scope)
                return t if t and is_set(t) else f"set[{t[:-2]}]" if t and t.endswith("[]") else "set[str]" if t=="str" else None
            if name=="range":return "i16[]"
            if name=="list":
                if not n["arguments"]:return "i16[]"
                t=expr(n["arguments"][0],scope);e=iterable_element(t) if t else None
                return e+"[]" if e else None
        if k=="method":
            t=expr(n["base"],scope)
            if t and is_object(t):
                name=classes[t[7:-1]]["methods"].get(n["name"]);f=functions.get(name)
                return f["return_type"] if f and f["return_type"]!="auto" else None
            if n["name"]=="copy":return t
            if t and is_set(t):return t if n["name"] in {"union","intersection","difference","symmetric_difference"} else element(t) if n["name"]=="pop" else "void"
            if t=="str":return "str[]" if n["name"]=="split" else "str"
            if t and t.endswith("[]") and n["name"]=="pop":return t[:-2]
            if t and is_dict(t) and n["name"] in {"get","pop","setdefault"}:return dict_types(t)[1]
        if k=="attribute":
            t=expr(n["base"],scope)
            if t and is_object(t):
                meta=classes[t[7:-1]]
                return meta["fields"].get(n["name"]) or f"closure[{meta['methods'][n['name']]}]" if n["name"] in meta["fields"] or n["name"] in meta["methods"] else None
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
                if declared=="auto":
                    combined=merge(scope.get(n["name"],n.get("inferred_type")),t)
                    if combined and combined.startswith("optional["):n["inferred_type"]=combined;t=combined
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
                elif t and is_iterator(t):elements=[iterable_element(t)]
                for name,element in zip(n.get("names",[n["name"]]),elements):scope[name]=element
                scan(n["body"],scope,returns)
            elif k in {"if","while","try_except","try_finally"}:
                scan(n["body"],scope,returns);scan(n.get("alternative",[]),scope,returns)
                scan(n.get("final",[]),scope,returns)
                for handler in n.get("handlers",[]):scan(handler["body"],scope,returns)
    def annotate(items,scope):
        for n in items:
            t=scope.get(n.get("name"),"")
            if n["kind"] in {"assign","declare"} and n.get("type","auto")=="auto":
                v=n["value"]
                if t.startswith("optional[") or v["kind"]=="list" and not v["values"] and t.endswith("[]"):n["inferred_type"]=t
            if n["kind"]!="function":
                for key in ("body","alternative","final"):
                    if isinstance(n.get(key),list):annotate(n[key],scope)
    for _ in range(max(2,len(functions)+1)):
        scan(tree["body"],globals_,[])
        annotate(tree["body"],globals_)
        for name,f in functions.items():
            if f.get("generator") and f.get("yield_type")=="auto":
                scope={p["name"]:norm(p["type"]) for p in f["params"]};scan(f["body"],scope,[])
                def yields(n):
                    if isinstance(n,dict):
                        if n.get("kind")=="yield":yield expr(n["value"],scope)
                        else:
                            for v in n.values():yield from yields(v)
                    elif isinstance(n,list):
                        for v in n:yield from yields(v)
                ts=list(yields(f["body"]))
                if ts and all(ts) and len(set(ts))==1:f["yield_type"]=ts[0]
        for name in automatic:
            f=functions[name];scope={**{k:v for k,v in f.get("capture_hints",{}).items() if v},**{p["name"]:norm(p["type"]) for p in f["params"]}};returns=[]
            scan(f["body"],scope,returns)
            annotate(f["body"],scope)
            if returns:
                t=returns[0]
                for other in returns[1:]:t=merge(t,other)
                if t:f["return_type"]=t
