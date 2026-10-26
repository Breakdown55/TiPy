"""Compile the browser's in-memory files; never read source paths from disk."""

import re

from .lexer import tokenize
from .parser import parse
from .checker import check, GRAPHICS_BUILTINS
from .codegen import generate
from .errors import CompileError
from .optimization_pipeline import optimize_program


def compile_project(files, entry, *, optimization="aggressive"):
    output = []
    locations = []
    visited = set()
    active = set()
    imports = {}
    selections = {}
    native_imports = {}

    def visit(name, origin):
        if name in active:
            raise CompileError(f"Circular import of {name}", origin)
        if name in visited:
            return
        if name not in files:
            raise CompileError(f"File not found: {name}", origin)
        active.add(name)
        imports.setdefault(name, set())
        selections.setdefault(name, {})
        from .stdlib import imports as extract_native
        try:
            source, native_imports[name] = extract_native(files[name])
        except CompileError as error:
            raise CompileError(error.message, {"filename": name, "line": error.line, "column": error.column}) from error
        for line_number, text in enumerate(source.splitlines(), 1):
            location = {"filename": name, "line": line_number, "column": 1}
            imported = re.fullmatch(r"import ([A-Za-z_]\w*)\s*(?:#.*)?", text)
            selected = re.fullmatch(r"from ([A-Za-z_]\w*) import (.+)", text.split("#", 1)[0].rstrip())
            if imported:
                imports[name].add(imported[1])
                visit(imported[1] + ".tip", location)
            elif selected:
                module, names = selected.groups()
                for binding in names.split(","):
                    match = re.fullmatch(r"\s*([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?\s*", binding)
                    if not match:
                        raise CompileError("Use from module import function[, other_function]", location)
                    function, alias = match[1], match[2] or match[1]
                    previous = selections[name].get(alias)
                    if previous and previous[:2] != (module, function):
                        raise CompileError(f"Import name {alias!r} is already bound", location)
                    selections[name][alias] = (module, function, location)
                visit(module + ".tip", location)
            else:
                output.append(text)
                locations.append(location)
        active.remove(name)
        visited.add(name)

    visit(entry, {"filename": entry, "line": 1, "column": 1})
    if not locations:
        locations.append({"filename": entry, "line": 1, "column": 1})

    def location_for(line):
        return locations[min(line - 1, len(locations) - 1)]

    try:
        tokens = tokenize("\n".join(output) + "\n", expand_defines=False)
    except CompileError as error:
        location = {**location_for(error.line), "column": error.column}
        raise CompileError(error.message, location) from error
    for token in tokens:
        location = location_for(token["line"])
        token["line"] = location["line"]
        token["filename"] = location["filename"]
    from .preprocessor import expand_tokens
    tokens = expand_tokens(tokens, {name: files[name] for name in visited})
    tree = parse(tokens)
    from .stdlib import resolve as resolve_native
    resolve_native(tree, native_imports)
    resolve_module_calls(tree, imports, selections)
    checked, symbols = check(tree)
    optimized, emitted_symbols, report = optimize_program(checked, symbols, optimization)
    return {"assembly": generate(optimized, emitted_symbols), "symbols": emitted_symbols,
            "source_symbols": symbols, "optimization_report": report}


def resolve_module_calls(tree, imports, selections=None):
    """Resolve file-qualified functions before ordinary type checking.

    Import remains a static include for global storage. Functions have unique
    per-file names; module qualification has no runtime object or lookup cost.
    Existing unqualified calls remain valid when their import is unambiguous.
    """
    exports = {filename: {} for filename in imports}
    selections = selections or {filename: {} for filename in imports}
    for statement in tree["body"]:
        if statement["kind"] != "function":
            continue
        filename, name = statement["filename"], statement["name"]
        if name in GRAPHICS_BUILTINS - {"clear"} or name == "len":
            raise CompileError(f"{name} is a reserved builtin", statement)
        if name in exports[filename]:
            raise CompileError(f"Function {name!r} is already defined", statement)
        qualified = filename[:-4] + "." + name
        exports[filename][name] = qualified
        statement["name"] = qualified

    for filename, bindings in selections.items():
        for alias, (module, name, origin) in bindings.items():
            if name not in exports[module + ".tip"]:
                raise CompileError(f"Module {module!r} has no function {name!r}", origin)
            if alias in exports[filename] or alias in imports[filename] or alias in GRAPHICS_BUILTINS or alias == "len":
                raise CompileError(f"Import name {alias!r} conflicts with an existing binding", origin)

    def resolve_bare(name, filename, item):
        if name in selections[filename]:
            module, exported, _ = selections[filename][name]
            return exports[module + ".tip"][exported]
        if name in exports[filename]:
            return exports[filename][name]
        candidates = [exports[module + ".tip"][name] for module in imports[filename]
                      if name in exports[module + ".tip"]]
        if len(candidates) > 1:
            raise CompileError(f"Ambiguous function {name!r}; use module.{name}(...) instead", item)
        return candidates[0] if candidates else name

    def bound_names(item):
        """Lexical bindings must win over file-qualified function exports."""
        names=set()
        def collect(value):
            if isinstance(value,list):
                for child in value:collect(child)
            elif isinstance(value,dict):
                kind=value.get("kind")
                if kind in {"function","class"}:
                    names.add(value["name"]);return
                if kind in {"lambda","comprehension"}:return
                if kind in {"declare","assign","target_name"}:names.add(value["name"])
                if kind in {"for","unpack_assign","chain_assign"}:names.update(value.get("names",[value.get("name")]))
                if kind=="except" and value.get("name"):names.add(value["name"])
                for child in value.values():collect(child)
        collect(item)
        return names

    module_bindings={filename:bound_names([n for n in tree["body"] if n.get("filename")==filename]) for filename in imports}

    def visit(item, lexical=frozenset()):
        if isinstance(item, list):
            for value in item:
                visit(value,lexical)
            return
        if not isinstance(item, dict):
            return
        filename = item.get("filename")
        if filename:
            lexical=lexical | module_bindings[filename]
            if item["kind"] in {"function","lambda"}:
                for parameter in item["params"]:
                    if parameter.get("default") is not None:visit(parameter["default"],lexical)
                body=item["body"] if item["kind"]=="function" else item["value"]
                local=bound_names(body)|{p["name"] for p in item["params"]}
                visit(body,lexical|local)
                return
            if item["kind"]=="comprehension":
                local=set(lexical)
                for clause in item["clauses"]:
                    visit(clause["iterable"],local)
                    local.update(clause["names"])
                    if clause.get("unpack_pattern"):local.update(bound_names(clause["unpack_pattern"]))
                    visit(clause["filters"],local)
                visit(item.get("key"),local);visit(item["value"],local)
                return
            if item["kind"] in {"declare", "parameter", "for", "assign"}:
                bindings = item.get("names", [item["name"]])
                for binding in bindings:
                    if binding in imports[filename]:
                        raise CompileError(f"{binding!r} is an imported module name", item)
                    if binding in selections[filename]:
                        raise CompileError(f"{binding!r} is an imported function name", item)
            if item["kind"] in {"method","attribute"} and item["base"]["kind"] == "name" and item["base"]["name"] in imports[filename]:
                module, name = item["base"]["name"], item["name"]
                qualified = exports[module + ".tip"].get(name)
                if qualified is None:
                    raise CompileError(f"Module {module!r} has no function {name!r}", item)
                del item["base"]
                item.update(kind="call" if item["kind"]=="method" else "name", name=qualified)
            elif item["kind"] in {"call","name"} and item["name"] not in lexical:
                item["name"] = resolve_bare(item["name"], filename, item)
        for value in list(item.values()):
            visit(value,lexical)

    visit(tree)
