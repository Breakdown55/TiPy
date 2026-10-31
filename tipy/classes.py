"""Lower statically laid-out classes to native records and ordinary methods."""
from .errors import CompileError
from .types import is_object,is_tuple,tuple_types,is_dict,dict_types


def prepare(tree):
    classes={};methods=[]
    def loc(n):return {k:n[k] for k in ("line","column","filename") if k in n}
    declarations=[n for n in tree["body"] if n["kind"]=="class"]
    names={n["name"] for n in declarations}
    def normalize(t):
        if t in names:return f"object[{t}]"
        if t.endswith("[]"):return normalize(t[:-2])+"[]"
        return "i16" if t=="int" else t
    def types(n):
        if isinstance(n,dict):
            for k in ("type","return_type"):
                if k in n:n[k]=normalize(n[k])
            for v in n.values():types(v)
        elif isinstance(n,list):
            for v in n:types(v)
    types(tree)
    for n in declarations:
        if n["name"] in classes:raise CompileError("Class is already defined",n)
        if len(n["bases"])>1:raise CompileError("Classes support one statically laid-out base",n)
        parent=classes.get(n["bases"][0]) if n["bases"] else None
        if n["bases"] and parent is None:raise CompileError("The base class must be declared first",n)
        meta={"name":n["name"],"fields":dict(parent["fields"]) if parent else {},"methods":dict(parent["methods"]) if parent else {},"base":n["bases"][0] if parent else None}
        meta["egx_shape"] = n.get("egx_shape", False)
        meta["readonly_fields"] = n.get("readonly_fields", [])
        classes[n["name"]]=meta
        for f in n["body"]:
            if f["kind"]=="pass":continue
            if f["kind"]!="function":raise CompileError("Declare instance fields in __init__",f)
            original=f["name"]
            if not f["params"]:raise CompileError("An instance method requires self",f)
            f["params"][0]["type"]=f"object[{n['name']}]"
            f["name"]=f"$method_{n['name']}_{original}";f["method_class"]=n["name"]
            meta["methods"][original]=f["name"];methods.append(f)
        if "__init__" not in meta["methods"]:
            name=f"$method_{n['name']}___init__";meta["methods"]["__init__"]=name
            methods.append({"kind":"function","name":name,"params":[{"kind":"parameter","name":"self","type":f"object[{n['name']}]",**loc(n)}],"return_type":"void","body":[{"kind":"pass",**loc(n)}],"method_class":n["name"],**loc(n)})
    def infer(n,scope):
        k=n["kind"]
        if k=="name":return scope.get(n["name"])
        if k in {"number","hash","cast_int"}:return "i16"
        if k in {"boolean","compare","identity","membership"}:return "bool"
        if k in {"string","fstring"}:return "str"
        if k=="decimal":
            from decimal import Decimal
            return "fixed" if Decimal('-128')<=Decimal(n['value'])<=Decimal('127.99609375') else "float"
        if k=="true_divide":return "float"
        if k in {"add","subtract","multiply","negate","floor_divide","modulo"}:return infer(n.get("left",n.get("value")),scope)
        if k=="list":
            t=infer(n["values"][0],scope) if n["values"] else "i16";return t+"[]" if t else None
        if k=="tuple":
            ts=[infer(v,scope) for v in n["values"]];return "tuple["+",".join(ts)+"]" if all(ts) else None
        if k=="call":
            if n["name"] in classes:return f"object[{n['name']}]"
            if n["name"] in {"str","input"}:return "str"
            if n["name"] in {"float","i32"}:return n["name"]
            if n["name"] in {"int","len","sum","abs","min","max"}:return "i16"
        if k=="attribute":
            t=infer(n["base"],scope)
            if t and is_object(t):return classes[t[7:-1]]["fields"].get(n["name"])
        if k=="index":
            t=infer(n["base"],scope)
            return t[:-2] if t and t.endswith("[]") else dict_types(t)[1] if t and is_dict(t) else None
        return None
    for _ in range(max(2,len(methods)+1)):
        for f in methods:
            scope={p["name"]:p["type"] for p in f["params"]};meta=classes[f["method_class"]]
            def scan(items):
                for n in items:
                    if n["kind"] in {"declare","assign"}:
                        t=normalize(n.get("type",infer(n["value"],scope) or "auto"))
                        if t!="auto":scope[n["name"]]=t
                    if n["kind"]=="attribute_assign" and n["target"]["base"].get("name")==f["params"][0]["name"]:
                        t=infer(n["value"],scope)
                        if t:meta["fields"].setdefault(n["target"]["name"],t)
                    for key in ("body","alternative"):
                        if isinstance(n.get(key),list):scan(n[key])
            scan(f["body"])
    for meta in classes.values():
        if meta["base"]:meta["fields"]={**classes[meta["base"]]["fields"],**meta["fields"]}
    tree["classes"]=classes
    body=[]
    for n in tree["body"]:
        if n["kind"]=="class":body.extend(f for f in methods if f["method_class"]==n["name"])
        else:body.append(n)
    tree["body"]=body
