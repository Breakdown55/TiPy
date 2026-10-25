"""Give comprehensions private targets and lower their clauses to ordinary loops."""


def prepare(tree):
    serial = 0

    def rewrite(item, scope):
        nonlocal serial
        if isinstance(item, list):
            for value in item:
                rewrite(value, scope)
        elif isinstance(item, dict):
            if item.get("kind") == "name" and item["name"] in scope:
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
                    rewrite(clause["filters"], local)
                rewrite(item["value"], local)
                location = {key: item[key] for key in ("line", "column", "filename") if key in item}
                collect = {"kind": "collect", "value": item["value"], **location}
                body = [collect]
                for clause in reversed(clauses):
                    for condition in reversed(clause.pop("filters")):
                        body = [{"kind": "if", "condition": condition, "body": body, "alternative": [], **location}]
                    clause["body"] = body
                    body = [clause]
                item["loop"], item["collect"] = body[0], collect
                return
            for value in item.values():
                rewrite(value, scope)

    rewrite(tree, {})
