"""Compile-time module bindings. These are native routines, not Python imports."""

import re
from .errors import CompileError

MEMBERS = {"time": {"sleep"}, "random": {"randint", "seed"},
           "math": {"radians", "sin", "cos"}}


def imports(source):
    bindings, lines = {}, []
    for text in source.splitlines():
        match = re.fullmatch(r"(\s*)import (time|random|math)(?:\s+as\s+(\w+))?\s*(?:#.*)?", text)
        selected = re.fullmatch(r"(\s*)from (time|random|math) import (.+)", text.split("#", 1)[0].rstrip())
        if match:
            indent, module, alias = match.groups()
            bindings[alias or module] = module
            text = indent + "pass"
        elif selected:
            indent, module, names = selected.groups()
            for binding in names.split(","):
                part = re.fullmatch(r"\s*(\w+)(?:\s+as\s+(\w+))?\s*", binding)
                if not part or part[1] not in MEMBERS[module]:
                    raise CompileError(f"Unsupported {module} import", {"line": len(lines) + 1, "column": 1})
                bindings[part[2] or part[1]] = module + "." + part[1]
            text = indent + "pass"
        lines.append(text)
    return "\n".join(lines) + "\n", bindings


def resolve(tree, bindings):
    def visit(item):
        if isinstance(item, list):
            for value in item: visit(value)
        elif isinstance(item, dict):
            scope = bindings.get(item.get("filename"), {})
            if item.get("kind") == "method" and item["base"]["kind"] == "name":
                module = scope.get(item["base"]["name"])
                if module in MEMBERS:
                    if item["name"] not in MEMBERS[module]:
                        raise CompileError(f"Unsupported {module} member {item['name']!r}", item)
                    item.update(kind="stdlib", name=module + "." + item["name"])
                    del item["base"]
            elif item.get("kind") == "call" and "." in scope.get(item["name"], ""):
                item.update(kind="stdlib", name=scope[item["name"]])
            targets = item.get("names", [item.get("name")])
            if item.get("kind") in {"declare", "parameter", "for", "assign", "function"} and any(target in scope for target in targets):
                raise CompileError("Cannot rebind a native module import", item)
            for value in list(item.values()): visit(value)
    visit(tree)
