"""Source text -> tokens. No grammar or variable lookup happens here."""

import re

from .errors import CompileError


TOKEN = re.compile(
    r"(?P<SPACE>[ \t]+)"
    r"|(?P<COMMENT>\#[^\n]*)"
    r"|(?P<NEWLINE>\n)"
    r'''|(?P<STRING>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')'''
    r"|(?P<DECIMAL>[0-9]+\.[0-9]+)"
    r"|(?P<NUMBER>0[xX][0-9a-fA-F]+|[0-9]+)"
    r"|(?P<NAME>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<OP>->|==|!=|<=|>=|//=|\*=|%=|\+=|-=|//|[=+*%\-()<>:,.\[\]{}])"
    r"|(?P<INVALID>.)"
)


def tokenize(source, *, expand_defines=True):
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens = []
    line, line_start = 1, 0
    indents = [0]
    at_line_start = True
    delimiters = []
    for match in TOKEN.finditer(source):
        kind = match.lastgroup
        text = match.group()
        token = {"kind": kind, "value": text, "line": line,
                 "column": match.start() - line_start + 1}
        if kind == "INVALID":
            message = "Semicolons are not used in TiPy" if text == ";" else f"Unexpected character {text!r}"
            raise CompileError(message, token)
        if kind not in {"SPACE", "COMMENT"}:
            if kind != "NEWLINE" and at_line_start and not delimiters:
                prefix = source[line_start:match.start()]
                if "\t" in prefix:
                    raise CompileError("Use spaces for indentation", token)
                width = len(prefix)
                if width > indents[-1]:
                    indents.append(width)
                    tokens.append({**token, "kind": "INDENT", "value": ""})
                else:
                    while width < indents[-1]:
                        indents.pop()
                        tokens.append({**token, "kind": "DEDENT", "value": ""})
                    if width != indents[-1]:
                        raise CompileError("Indentation does not match an outer block", token)
                at_line_start = False
            if kind != "NEWLINE":
                at_line_start = False
            if kind == "NAME" and text in {"int", "i16", "u8", "bool", "void", "str", "dict"}:
                token["kind"] = "TYPE"
            elif kind == "NAME" and text in {"True", "False"}:
                token["kind"] = "BOOL"
            elif kind == "NAME" and text in {"if", "elif", "else", "pass", "while", "break", "continue", "def", "return", "for", "in", "and", "or", "not", "del"}:
                token["kind"] = text.upper()
            elif kind == "NUMBER":
                token["value"] = int(text, 16 if text.lower().startswith("0x") else 10)
            if kind != "NEWLINE" or not delimiters:
                tokens.append(token)
            if kind == "OP" and text in {"(", "[", "{"}:
                delimiters.append(text)
            elif kind == "OP" and text in {")", "]", "}"}:
                if delimiters and delimiters[-1] == {")": "(", "]": "[", "}": "{"}[text]:
                    delimiters.pop()
        if kind == "NEWLINE":
            line += 1
            line_start = match.end()
            at_line_start = True
    end = {"line": line, "column": len(source) - line_start + 1}
    if tokens and tokens[-1]["kind"] != "NEWLINE":
        tokens.append({"kind": "NEWLINE", "value": "\n", **end})
    while len(indents) > 1:
        indents.pop()
        tokens.append({"kind": "DEDENT", "value": "", **end})
    tokens.append({"kind": "EOF", "value": "", **end})
    if expand_defines:
        from .preprocessor import expand_tokens
        return expand_tokens(tokens, {None: source})
    return tokens
