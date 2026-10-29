"""Compile-time module bindings. These are native routines, not Python imports."""

import re
from .errors import CompileError

MEMBERS = {"time": {"sleep","frame_ticks","stop_frame_timer"}, "random": {"randint", "seed"},
           "math": {"radians", "sin", "cos"}}
from .graphics_library import PUBLIC
from .toolkit_namespace import ALIASES
MEMBERS['py5']=PUBLIC|{'line','BLACK','WHITE','CLOSE','OPEN'}
MEMBERS.update({name:set(members) for name,members in ALIASES.items()})


def imports(source):
    bindings, lines = {}, []
    for text in source.splitlines():
        modules = "|".join(MEMBERS)
        match = re.fullmatch(r"(\s*)import ("+modules+r")(?:\s+as\s+(\w+))?\s*(?:#.*)?", text)
        selected = re.fullmatch(r"(\s*)from ("+modules+r") import (.+)", text.split("#", 1)[0].rstrip())
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
                    item.update(kind="call" if module=='py5' or module in ALIASES else "stdlib", name=ALIASES[module][item['name']] if module in ALIASES else item['name'] if module=='py5' else module + "." + item["name"])
                    del item["base"]
            elif item.get("kind") == "call" and "." in scope.get(item["name"], ""):
                target=scope[item['name']];module,member=target.split('.',1)
                item.update(kind='call' if module=='py5' or module in ALIASES else 'stdlib',name=ALIASES[module][member] if module in ALIASES else member if module=='py5' else target)
            constants={'BLACK':0,'WHITE':255,'CLOSE':True,'OPEN':False}
            constant=None
            if item.get('kind')=='attribute' and item['base'].get('kind')=='name' and scope.get(item['base']['name'])=='py5':
                if item['name'] not in constants:raise CompileError('Unsupported py5 attribute',item)
                constant=item['name']
            elif item.get('kind')=='name' and scope.get(item['name'],'').startswith('py5.'):
                constant=scope[item['name']][4:]
            if constant in constants:
                value=constants[constant];location={k:item[k] for k in ('filename','line','column') if k in item};item.clear();item.update(kind='boolean' if isinstance(value,bool) else 'number',value=value,**location)
            targets = item.get("names", [item.get("name")])
            if item.get("kind") in {"declare", "parameter", "for", "assign", "function"} and any(target in scope for target in targets):
                raise CompileError("Cannot rebind a native module import", item)
            for value in list(item.values()): visit(value)
    visit(tree)
