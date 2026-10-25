"""File-scoped object-like numeric #define constants, expanded as tokens."""

import re
from .errors import CompileError

DIRECTIVE = re.compile(r"^\s*#define\b(.*)$")
DEFINITION = re.compile(r"\s+([A-Za-z_]\w*)[ \t]+(.+?)\s*$")
OPERATORS = {"(", ")", "+", "-", "*", "//", "%", "==", "!=", "<", ">", "<=", ">="}


def expand_tokens(tokens, sources):
    from .lexer import tokenize
    definitions = {}
    for filename, source in sources.items():
        entries = []
        for line_number, line in enumerate(source.splitlines(), 1):
            directive = DIRECTIVE.fullmatch(line)
            if not directive: continue
            origin = {"line": line_number, "column": line.index("#") + 1}
            if filename is not None: origin["filename"] = filename
            match = DEFINITION.fullmatch(directive[1])
            if not match:
                raise CompileError("Use #define NAME constant_expression", origin)
            name, value = match.groups()
            name_token = tokenize(name, expand_defines=False)[0]
            if name_token["kind"] != "NAME":
                raise CompileError("A #define name cannot be a keyword or type", origin)
            try:
                value_tokens = [t for t in tokenize(value, expand_defines=False) if t["kind"] not in {"NEWLINE", "EOF"}]
            except CompileError as error:
                raise CompileError(error.message, origin) from error
            depth = 0
            for token in value_tokens:
                if token["kind"] not in {"NUMBER", "BOOL", "NAME", "OP", "AND", "OR", "NOT"} or token["kind"] == "OP" and token["value"] not in OPERATORS:
                    raise CompileError("#define requires a numeric or boolean constant expression", origin)
                if token["value"] == "(": depth += 1
                elif token["value"] == ")": depth -= 1
                if depth < 0: raise CompileError("Unbalanced parentheses in #define", origin)
            if not value_tokens or depth:
                raise CompileError("Invalid or unbalanced #define expression", origin)
            entries.append((line_number, name, value_tokens, origin))
        definitions[filename] = entries

    visible = {filename: {} for filename in sources}
    cursors = {filename: 0 for filename in sources}
    output = []

    def expand(name, macros, origin, chain=()):
        if name in chain or len(chain) >= 32:
            raise CompileError("Recursive or too deeply nested #define expansion", origin)
        if name not in macros:
            raise CompileError(f"Unknown constant {name!r} in #define expression", origin)
        result = [{**origin, "kind": "OP", "value": "("}]
        for token in macros[name]:
            if token["kind"] == "NAME":
                result.extend(expand(token["value"], macros, origin, (*chain, name)))
            else:
                result.append({**origin, "kind": token["kind"], "value": token["value"]})
            if len(result) > 4096:
                raise CompileError("#define expansion is too large", origin)
        result.append({**origin, "kind": "OP", "value": ")"})
        return result

    previous = None
    for token in tokens:
        filename = token.get("filename")
        entries, macros = definitions.get(filename, []), visible.setdefault(filename, {})
        cursor = cursors.get(filename, 0)
        while cursor < len(entries) and entries[cursor][0] <= token["line"]:
            _, name, value, origin = entries[cursor]
            if name in macros and [(t["kind"], t["value"]) for t in macros[name]] != [(t["kind"], t["value"]) for t in value]:
                raise CompileError(f"Constant {name!r} is already defined", origin)
            macros[name] = value
            cursor += 1
        cursors[filename] = cursor
        if token["kind"] == "NAME" and token["value"] in macros and not (previous and previous["kind"] == "OP" and previous["value"] == "."):
            output.extend(expand(token["value"], macros, {key: token[key] for key in ("line", "column", "filename") if key in token}))
        else:
            output.append(token)
        if len(output) > 1000000:
            raise CompileError("Expanded source is too large", token)
        previous = token
    return output
