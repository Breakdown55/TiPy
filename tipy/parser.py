"""Tokens -> dictionary nodes. The parser checks structure, not types."""

from .errors import CompileError


def node(kind, token, **fields):
    result = {"kind": kind, **fields, "line": token["line"], "column": token["column"]}
    if "filename" in token:
        result["filename"] = token["filename"]
    return result


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    @property
    def current(self):
        return self.tokens[self.index]

    def take(self, kind, value=None):
        token = self.current
        if token["kind"] != kind or (value is not None and token["value"] != value):
            raise CompileError(f"Expected {value or kind.lower()}, got {token['value']!r}", token)
        self.index += 1
        return token

    def program(self):
        return {"kind": "program", "body": self.statements("EOF")}

    def statements(self, stop):
        statements = []
        while self.current["kind"] != stop:
            if self.current["kind"] == "NEWLINE":
                self.take("NEWLINE")
                continue
            statements.append(self.statement())
        return statements

    def suite(self):
        self.take("OP", ":")
        self.take("NEWLINE")
        while self.current["kind"] == "NEWLINE":
            self.take("NEWLINE")
        self.take("INDENT")
        body = self.statements("DEDENT")
        self.take("DEDENT")
        return body

    def statement(self):
        start = self.current
        if start["kind"] == "DEF":
            self.take("DEF")
            name = self.take("NAME")["value"]
            self.take("OP", "(")
            params = []
            if self.current["value"] != ")":
                while True:
                    parameter = self.current
                    inferred = parameter["kind"] == "NAME"
                    parameter_type = "int" if inferred else self.take("TYPE")["value"]
                    if self.current["value"] == "[":
                        self.take("OP", "[")
                        self.take("OP", "]")
                        parameter_type += "[]"
                    parameter_name = self.take("NAME")["value"]
                    params.append(node("parameter", parameter, type=parameter_type, name=parameter_name))
                    if self.current["value"] != ",":
                        break
                    self.take("OP", ",")
            self.take("OP", ")")
            return_type = "auto"
            if self.current["value"] == "->":
                self.take("OP", "->")
                return_type = self.take("TYPE")["value"]
            if self.current["value"] == "[":
                self.take("OP", "[")
                self.take("OP", "]")
                return_type += "[]"
            return node("function", start, name=name, params=params,
                        return_type=return_type, body=self.suite())
        if start["kind"] == "RETURN":
            self.take("RETURN")
            value = None if self.current["kind"] == "NEWLINE" else self.expression()
            self.take("NEWLINE")
            return node("return", start, value=value)
        if start["kind"] == "FOR":
            self.take("FOR")
            names = self.targets()
            self.take("IN")
            iterable = self.expression()
            return node("for", start, name=names[0], names=names, iterable=iterable, body=self.suite())
        if start["kind"] == "WHILE":
            self.take("WHILE")
            condition = self.expression()
            body = self.suite()
            return node("while", start, condition=condition, body=body)
        if start["kind"] in {"BREAK", "CONTINUE"}:
            self.take(start["kind"])
            self.take("NEWLINE")
            return node(start["value"], start)
        if start["kind"] == "IF":
            return self.if_statement()
        if start["kind"] == "PASS":
            self.take("PASS")
            self.take("NEWLINE")
            return node("pass", start)
        if start["kind"] == "TYPE":
            type_name = self.take("TYPE")["value"]
            name = self.take("NAME")["value"]
            self.take("OP", "=")
            result = node("declare", start, type=type_name, name=name, value=self.expression())
            self.take("NEWLINE")
            return result
        name = self.take("NAME")["value"]
        if self.current["value"] in {"[", "."}:
            target = self.postfix(node("name", start, name=name))
            if target["kind"] == "method":
                self.take("NEWLINE")
                return node("expression_statement", start, value=target)
            if target["kind"] != "index":
                raise CompileError("Expected an array element assignment", start)
            operator = self.take("OP")
            if operator["value"] not in {"=", "+=", "-=", "*=", "//=", "%="}:
                raise CompileError("Expected =, += or -=", operator)
            value = self.expression()
            self.take("NEWLINE")
            return node("index_assign", start, target=target, operator=operator["value"], value=value)
        if self.current["value"] == "(":
            call = self.call(start)
            self.take("NEWLINE")
            return node("expression_statement", start, value=call)
        operator = self.take("OP")
        if operator["value"] not in {"=", "+=", "-=", "*=", "//=", "%="}:
            raise CompileError("Expected =, += or -=", operator)
        value = self.expression()
        # Lower compound assignment into the same tree used by ordinary assignment.
        if operator["value"] != "=":
            kind = {"+=": "add", "-=": "subtract", "*=": "multiply", "//=": "floor_divide", "%=": "modulo"}[operator["value"]]
            value = node(kind, operator, left=node("name", start, name=name), right=value)
        self.take("NEWLINE")
        return node("assign", start, name=name, value=value, inferred=operator["value"] == "=")

    def expression(self):
        result = self.boolean_and()
        while self.current["kind"] == "OR":
            token = self.take("OR")
            result = node("or", token, left=result, right=self.boolean_and())
        return result

    def boolean_and(self):
        result = self.boolean_not()
        while self.current["kind"] == "AND":
            token = self.take("AND")
            result = node("and", token, left=result, right=self.boolean_not())
        return result

    def boolean_not(self):
        if self.current["kind"] == "NOT":
            token = self.take("NOT")
            return node("not", token, value=self.boolean_not())
        return self.comparison()

    def if_statement(self):
        start = self.current
        self.take(start["kind"])
        condition, body = self.expression(), self.suite()
        alternative = []
        if self.current["kind"] == "ELIF":
            alternative = [self.if_statement()]
        elif self.current["kind"] == "ELSE":
            self.take("ELSE"); alternative = self.suite()
        return node("if", start, condition=condition, body=body, alternative=alternative)

    def comparison(self):
        result = self.sum()
        if self.current["kind"] == "IN" or self.current["kind"] == "NOT" and self.tokens[self.index + 1]["kind"] == "IN":
            token = self.current
            negate = self.current["kind"] == "NOT"
            if negate: self.take("NOT")
            self.take("IN")
            return node("membership", token, left=result, right=self.sum(), negate=negate)
        if self.current["kind"] == "OP" and self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
            operator = self.take("OP")
            result = node("compare", operator, operator=operator["value"], left=result, right=self.sum())
            if self.current["kind"] == "OP" and self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
                operands = [result["left"], result["right"]]
                operators = [result["operator"]]
                while self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
                    operators.append(self.take("OP")["value"])
                    operands.append(self.sum())
                result = node("compare_chain", operator, operands=operands, operators=operators)
        return result

    def sum(self):
        result = self.term()
        # + and - have equal precedence and associate left to right.
        while self.current["kind"] == "OP" and self.current["value"] in {"+", "-"}:
            operator = self.take("OP")
            result = node("add" if operator["value"] == "+" else "subtract", operator,
                          left=result, right=self.term())
        return result

    def term(self):
        result = self.atom()
        while self.current["value"] in {"%", "*", "//"}:
            operator = self.take("OP")
            result = node({"%": "modulo", "*": "multiply", "//": "floor_divide"}[operator["value"]], operator, left=result, right=self.atom())
        return result

    def atom(self):
        token = self.current
        if token["kind"] == "DECIMAL":
            self.take("DECIMAL")
            return node("decimal", token, value=token["value"])
        if token["kind"] == "TYPE" and token["value"] == "int":
            self.take("TYPE")
            return self.call(token)
        if token["kind"] == "NUMBER":
            self.take("NUMBER")
            return node("number", token, value=token["value"])
        if token["kind"] == "BOOL":
            self.take("BOOL")
            return node("boolean", token, value=token["value"] == "True")
        if token["kind"] == "NAME":
            self.take("NAME")
            if self.current["value"] == "(":
                return self.postfix(self.call(token))
            return self.postfix(node("name", token, name=token["value"]))
        if token["value"] == "[":
            self.take("OP", "[")
            values = []
            if self.current["value"] != "]":
                values.append(self.expression())
                if self.current["kind"] == "FOR":
                    clauses = []
                    while self.current["kind"] == "FOR":
                        start = self.take("FOR")
                        names = self.targets(); self.take("IN")
                        iterable = self.expression()
                        filters = []
                        while self.current["kind"] == "IF":
                            self.take("IF"); filters.append(self.expression())
                        clauses.append(node("for", start, name=names[0], names=names, iterable=iterable, filters=filters, body=[]))
                    self.take("OP", "]")
                    return self.postfix(node("comprehension", token, value=values[0], clauses=clauses))
                while True:
                    if self.current["value"] != ",":
                        break
                    self.take("OP", ",")
                    if self.current["value"] == "]":
                        break
                    values.append(self.expression())
            self.take("OP", "]")
            return self.postfix(node("list", token, values=values))
        if token["kind"] == "OP" and token["value"] in {"+", "-"}:
            self.take("OP")
            operand = self.atom()
            return node("negate", token, value=operand) if token["value"] == "-" else operand
        if token["kind"] == "OP" and token["value"] == "(":
            self.take("OP", "(")
            if self.current["value"] == ")":
                self.take("OP", ")")
                return self.postfix(node("tuple", token, values=[]))
            result = self.expression()
            if self.current["value"] == ",":
                values = [result]
                while self.current["value"] == ",":
                    self.take("OP", ",")
                    if self.current["value"] == ")": break
                    values.append(self.expression())
                result = node("tuple", token, values=values)
            self.take("OP", ")")
            return self.postfix(result)
        raise CompileError("Expected a number, variable or parenthesized expression", token)

    def postfix(self, result):
        while self.current["value"] in {"[", "."}:
            token = self.current
            if token["value"] == "[":
                self.take("OP", "[")
                index = self.expression()
                self.take("OP", "]")
                result = node("index", token, base=result, index=index)
            else:
                self.take("OP", ".")
                method = self.take("NAME")
                call = self.call(method)
                result = node("method", token, base=result, name=method["value"], arguments=call["arguments"])
        return result

    def targets(self):
        names = [self.take("NAME")["value"]]
        while self.current["value"] == ",":
            self.take("OP", ",")
            names.append(self.take("NAME")["value"])
        return names

    def call(self, name):
        self.take("OP", "(")
        arguments = []
        if self.current["value"] != ")":
            while True:
                arguments.append(self.expression())
                if self.current["value"] != ",":
                    break
                self.take("OP", ",")
        self.take("OP", ")")
        return node("call", name, name=name["value"], arguments=arguments)


def parse(tokens):
    return Parser(tokens).program()
