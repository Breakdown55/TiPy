"""Give comprehensions private targets and lower their clauses to ordinary loops."""


def prepare(tree):
    serial = 0

    def rewrite(item, scope):
        nonlocal serial
        if isinstance(item, list):
            for value in item:
                rewrite(value, scope)
        elif isinstance(item, dict):
            if item.get("kind") in {"name","target_name"} and item["name"] in scope:
                item["name"] = scope[item["name"]]
            if item.get("kind") == "comprehension":
                local = scope.copy()
                clauses = item.pop("clauses")
                for clause in clauses:
                    rewrite(clause["iterable"], local)
                    targets = []
                    for name in clause["names"]:
                        serial += 1
                        private = f"$comp_{serial}_{name}"
                        local[name] = private
                        targets.append(private)
                    clause["name"], clause["names"] = targets[0], targets
                    if clause.get("unpack_pattern"):
                        def names(n):
                            if isinstance(n,dict):
                                if n.get("kind")=="target_name":yield n["name"]
                                else:
                                    for v in n.values():yield from names(v)
                            elif isinstance(n,list):
                                for v in n:yield from names(v)
                        for name in names(clause["unpack_pattern"]):
                            serial+=1;local[name]=f"$comp_{serial}_{name}"
                        rewrite(clause["unpack_pattern"],local)
                    rewrite(clause["filters"], local)
                rewrite(item["value"], local)
                if "key" in item:rewrite(item["key"],local)
                location = {key: item[key] for key in ("line", "column", "filename") if key in item}
                collect = {"kind": "collect", "value": item["value"], "collection":item.get("collection","list"), **location}
                if "key" in item:collect["key"]=item["key"]
                body = [collect]
                for clause in reversed(clauses):
                    for condition in reversed(clause.pop("filters")):
                        body = [{"kind": "if", "condition": condition, "body": body, "alternative": [], **location}]
                    clause["body"] = body
                    if clause.get("unpack_pattern"):
                        clause["body"]=[{"kind":"pattern_assign","target":clause.pop("unpack_pattern"),"value":{"kind":"name","name":clause["name"],**location},**location},*body]
                    body = [clause]
                item["loop"], item["collect"] = body[0], collect
                return
            for value in item.values():
                rewrite(value, scope)

    rewrite(tree, {})
