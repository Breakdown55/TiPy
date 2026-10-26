"""Lift lexical functions; captured variables keep shared, native heap cells."""
from .errors import CompileError


def prepare(tree):
    serial=0;lifted=[]
    def loc(n):return {k:n[k] for k in ("line","column","filename") if k in n}
    def defaults(items,nested=False):
        nonlocal serial
        output=[]
        for n in items:
            if n["kind"]=="function":
                if nested:
                    for p in n["params"]:
                        if p.get("default") is not None:
                            serial+=1;name=f"$nested_default_{serial}"
                            output.append({"kind":"assign","inferred":True,"name":name,"value":p.pop("default"),**loc(n)})
                            p["default_capture"]=name
                n["body"]=defaults(n["body"],True)
            else:
                for key in ("body","alternative","final"):
                    if isinstance(n.get(key),list):n[key]=defaults(n[key],nested)
                for h in n.get("handlers",[]):h["body"]=defaults(h["body"],nested)
            output.append(n)
        return output
    tree["body"]=defaults(tree["body"])
    def scope(body,params):
        local={p["name"] for p in params};reads=set();nonlocal_=set();children=[]
        def walk(n):
            if isinstance(n,list):
                for v in n:walk(v)
            elif isinstance(n,dict):
                k=n.get("kind")
                if k in {"function","lambda"}:
                    if k=="function":local.add(n["name"])
                    child_body=n["body"] if k=="function" else [{"kind":"return","value":n["value"],**loc(n)}]
                    own,free=scope(child_body,n["params"]);free.update(p["default_capture"] for p in n["params"] if "default_capture" in p)
                    n["lexical_locals"]=sorted(own);n["lexical_free"]=sorted(free);children.append(n)
                    return
                if k in {"declare","assign"}:local.add(n["name"])
                if k in {"for","unpack_assign"}:local.update(n.get("names",[n.get("name")]))
                if k=="nonlocal":nonlocal_.update(n["names"])
                if k in {"name","call"}:reads.add(n["name"])
                for v in n.values():walk(v)
        walk(body);local-=nonlocal_
        for child in children:reads.update(child["lexical_free"])
        return local,(reads-local)|nonlocal_
    scope(tree["body"],[])
    def rewrite(n,ancestors,current=None):
        nonlocal serial
        if isinstance(n,list):
            return [rewrite(v,ancestors,current) for v in n]
        if not isinstance(n,dict):return n
        k=n.get("kind")
        if k=="nonlocal":
            if not current or any(name not in current.get("captures",[]) for name in n["names"]):raise CompileError("nonlocal requires a binding in an enclosing function",n)
            return {"kind":"pass",**loc(n)}
        if k in {"function","lambda"}:
            nested=current is not None or k=="lambda"
            if k=="lambda":n={**n,"kind":"function","name":"$lambda","return_type":"auto","body":[{"kind":"return","value":n["value"],**loc(n)}]}
            local=set(n.pop("lexical_locals",[]));free=set(n.pop("lexical_free",[]))
            captures=sorted(free & set().union(*ancestors)) if ancestors else []
            n["captures"]=captures;n["cell_names"]=[]
            for child in descendants(n["body"]):n["cell_names"].extend(set(child.get("lexical_free",[])) & local)
            n["cell_names"]=sorted(set(n["cell_names"]))
            if nested:
                serial+=1;original=n["name"];n["name"]=f"$closure_{serial}"
                n["params"]=[{"kind":"parameter","name":"$env","type":"tuple[]",**loc(n)},*n["params"]];n["closure_env"]=True
                lifted.append(n)
            n["body"]=rewrite(n["body"],[*ancestors,local],n)
            if not nested:return n
            value={"kind":"closure","function":n["name"],"captures":[{"kind":"cell_ref","name":name,**loc(n)} for name in captures],**loc(n)}
            if k=="lambda":return value
            return {"kind":"assign","name":original,"value":value,"inferred":True,**loc(n)}
        return {key:rewrite(value,ancestors,current) for key,value in n.items()}
    def descendants(items):
        for n in items:
            if n.get("kind") in {"function","lambda"}:yield n;continue
            for key in ("body","alternative"):
                if isinstance(n.get(key),list):yield from descendants(n[key])
            def expressions(v):
                if isinstance(v,dict):
                    if v.get("kind")=="lambda":yield v
                    else:
                        for x in v.values():yield from expressions(x)
                elif isinstance(v,list):
                    for x in v:yield from expressions(x)
            for key,value in n.items():
                if key not in {"body","alternative"}:yield from expressions(value)
    tree["body"]=rewrite(tree["body"],[])+lifted
