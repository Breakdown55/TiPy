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

    def type_name(self):
        name = self.take("NAME" if self.current["kind"]=="NAME" else "TYPE")["value"]
        if name in {"dict","tuple","set"}:
            if self.current["value"] != "[": return {"dict":"dict[int,int]","tuple":"tuple[]","set":"set[int]"}[name]
            self.take("OP", "[")
            fields=[]
            if self.current["value"]!="]":
                fields.append(self.type_name())
                while self.current["value"]==",":
                    self.take("OP",",");fields.append(self.type_name())
            self.take("OP", "]")
            if name=="dict" and len(fields)!=2:raise CompileError("dict requires key and value types",self.current)
            if name=="set" and len(fields)!=1:raise CompileError("set requires one element type",self.current)
            name=name+"["+",".join(fields)+"]"
        while self.current["value"]=="[" and self.tokens[self.index+1]["value"]=="]":
            self.take("OP","[");self.take("OP","]");name+="[]"
        return name

    def expression_list(self):
        start=self.current;value=self.expression()
        if self.current["value"]!=",":return value
        values=[value]
        while self.current["value"]==",":
            self.take("OP",",")
            if self.current["kind"]=="NEWLINE":break
            values.append(self.expression())
        return node("tuple",start,values=values)

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
        if start["kind"]=="CLASS":
            self.take("CLASS");name=self.take("NAME")["value"];bases=[]
            if self.current["value"]=="(":
                self.take("OP","(")
                if self.current["value"]!=")":bases=self.targets()
                self.take("OP",")")
            return node("class",start,name=name,bases=bases,body=self.suite())
        if start["kind"]=="YIELD":
            self.take("YIELD");value=node("none",start) if self.current["kind"]=="NEWLINE" else self.expression_list()
            self.take("NEWLINE");return node("yield",start,value=value)
        if start["kind"]=="TRY":
            self.take("TRY");body=self.suite();handlers=[];alternative=[];final=[]
            while self.current["kind"]=="EXCEPT":
                token=self.take("EXCEPT");types=[];name=None
                if self.current["value"]!=":":
                    if self.current["value"]=="(":
                        self.take("OP","(");types.append(self.take("NAME")["value"])
                        while self.current["value"]==",":self.take("OP",",");types.append(self.take("NAME")["value"])
                        self.take("OP",")")
                    else:types.append(self.take("NAME")["value"])
                    if self.current["kind"]=="AS":self.take("AS");name=self.take("NAME")["value"]
                handlers.append(node("except",token,types=types,name=name,body=self.suite()))
            if self.current["kind"]=="ELSE":self.take("ELSE");alternative=self.suite()
            if self.current["kind"]=="FINALLY":self.take("FINALLY");final=self.suite()
            if not handlers and not final:raise CompileError("try requires except or finally",start)
            if alternative and not handlers:raise CompileError("try else requires except",start)
            result=node("try_except",start,body=body,handlers=handlers,alternative=alternative) if handlers else None
            return node("try_finally",start,body=[result] if result else body,final=final) if final else result
        if start["kind"] in {"RAISE","ASSERT"}:
            self.take(start["kind"]);value=None if self.current["kind"]=="NEWLINE" else self.expression();message=None
            if self.current["value"]==",":self.take("OP",",");message=self.expression()
            self.take("NEWLINE");return node(start["value"],start,value=value,message=message)
        if start["kind"]=="NONLOCAL":
            self.take("NONLOCAL");names=self.targets();self.take("NEWLINE")
            return node("nonlocal",start,names=names)
        if start["kind"] == "DEF":
            self.take("DEF")
            name = self.take("NAME")["value"]
            self.take("OP", "(")
            params = []
            if self.current["value"] != ")":
                while True:
                    parameter = self.current
                    variadic=False
                    if self.current["value"]=="*":self.take("OP","*");variadic=True
                    parameter = self.current
                    inferred = parameter["kind"] == "NAME" and self.tokens[self.index+1]["kind"]!="NAME"
                    parameter_type = "int" if inferred else self.type_name()
                    if self.current["value"] == "[":
                        self.take("OP", "[")
                        self.take("OP", "]")
                        parameter_type += "[]"
                    parameter_name = self.take("NAME")["value"]
                    default=None
                    if self.current["value"]=="=":
                        self.take("OP","=");default=self.expression()
                    params.append(node("parameter", parameter, type=parameter_type, name=parameter_name,default=default,variadic=variadic))
                    if self.current["value"] != ",":
                        break
                    self.take("OP", ",")
            self.take("OP", ")")
            return_type = "auto"
            if self.current["value"] == "->":
                self.take("OP", "->")
                return_type = self.type_name()
            if self.current["value"] == "[":
                self.take("OP", "[")
                self.take("OP", "]")
                return_type += "[]"
            return node("function", start, name=name, params=params,
                        return_type=return_type, body=self.suite())
        if start["kind"] == "RETURN":
            self.take("RETURN")
            value = None if self.current["kind"] == "NEWLINE" else self.expression_list()
            self.take("NEWLINE")
            return node("return", start, value=value)
        if start["kind"] == "DEL":
            self.take("DEL")
            target = self.expression()
            self.take("NEWLINE")
            return node("dict_delete", start, target=target)
        if start["kind"] == "FOR":
            self.take("FOR")
            names = self.targets()
            self.take("IN")
            iterable = self.expression()
            body=self.suite();alternative=[]
            if self.current["kind"]=="ELSE":self.take("ELSE");alternative=self.suite()
            return node("for", start, name=names[0], names=names, iterable=iterable, body=body,alternative=alternative)
        if start["kind"] == "WHILE":
            self.take("WHILE")
            condition = self.expression()
            body = self.suite()
            alternative=[]
            if self.current["kind"]=="ELSE":self.take("ELSE");alternative=self.suite()
            return node("while", start, condition=condition, body=body,alternative=alternative)
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
        if start["kind"] == "TYPE" or start["kind"]=="NAME" and self.tokens[self.index+1]["kind"]=="NAME":
            type_name = self.type_name()
            name = self.take("NAME")["value"]
            self.take("OP", "=")
            result = node("declare", start, type=type_name, name=name, value=self.expression())
            self.take("NEWLINE")
            return result
        name = self.take("NAME")["value"]
        if self.current["value"]==",":
            names=[name]
            while self.current["value"]==",":
                self.take("OP",",");names.append(self.take("NAME")["value"])
            self.take("OP","=");value=self.expression_list();self.take("NEWLINE")
            return node("unpack_assign",start,names=names,value=value)
        if self.current["value"] in {"[", "."}:
            target = self.postfix(node("name", start, name=name))
            if target["kind"] == "method":
                self.take("NEWLINE")
                return node("expression_statement", start, value=target)
            if target["kind"] not in {"index","attribute"}:
                raise CompileError("Expected an array element assignment", start)
            operator = self.take("OP")
            if operator["value"] not in {"=", "+=", "-=", "*=", "//=", "%=", "&=", "|=", "^=", "<<=", ">>=", "**="}:
                raise CompileError("Expected =, += or -=", operator)
            value = self.expression()
            self.take("NEWLINE")
            return node("attribute_assign" if target["kind"]=="attribute" else "index_assign", start, target=target, operator=operator["value"], value=value)
        if self.current["value"] == "(":
            call = self.call(start)
            self.take("NEWLINE")
            return node("expression_statement", start, value=call)
        operator = self.take("OP")
        if operator["value"] not in {"=", "+=", "-=", "*=", "//=", "%=", "&=", "|=", "^=", "<<=", ">>=", "**="}:
            raise CompileError("Expected =, += or -=", operator)
        value = self.expression_list()
        # Lower compound assignment into the same tree used by ordinary assignment.
        if operator["value"] != "=":
            kind = {"+=": "add", "-=": "subtract", "*=": "multiply", "//=": "floor_divide", "%=": "modulo", "&=":"bit_and", "|=":"bit_or", "^=":"bit_xor", "<<=":"shift_left", ">>=":"shift_right", "**=":"power"}[operator["value"]]
            value = node(kind, operator, left=node("name", start, name=name), right=value)
        self.take("NEWLINE")
        return node("assign", start, name=name, value=value, inferred=operator["value"] == "=")

    def expression(self,conditional=True):
        if self.current["kind"]=="LAMBDA":
            token=self.take("LAMBDA");params=[]
            if self.current["value"]!=":":
                while True:
                    p=self.current;t=self.type_name() if p["kind"]=="TYPE" else "int";name=self.take("NAME")["value"]
                    params.append(node("parameter",p,type=t,name=name))
                    if self.current["value"]!=",":break
                    self.take("OP",",")
            self.take("OP",":")
            return node("lambda",token,params=params,value=self.expression())
        result = self.boolean_and()
        while self.current["kind"] == "OR":
            token = self.take("OR")
            result = node("or", token, left=result, right=self.boolean_and())
        if conditional and self.current["kind"]=="IF":
            token=self.take("IF");condition=self.expression(False);self.take("ELSE")
            result=node("conditional",token,condition=condition,body=result,alternative=self.expression())
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
        result = self.bit_or()
        if self.current["kind"]=="IS":
            token=self.take("IS");negate=False
            if self.current["kind"]=="NOT":self.take("NOT");negate=True
            return node("identity",token,left=result,right=self.bit_or(),negate=negate)
        if self.current["kind"] == "IN" or self.current["kind"] == "NOT" and self.tokens[self.index + 1]["kind"] == "IN":
            token = self.current
            negate = self.current["kind"] == "NOT"
            if negate: self.take("NOT")
            self.take("IN")
            return node("membership", token, left=result, right=self.bit_or(), negate=negate)
        if self.current["kind"] == "OP" and self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
            operator = self.take("OP")
            result = node("compare", operator, operator=operator["value"], left=result, right=self.bit_or())
            if self.current["kind"] == "OP" and self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
                operands = [result["left"], result["right"]]
                operators = [result["operator"]]
                while self.current["value"] in {"==", "!=", "<", ">", "<=", ">="}:
                    operators.append(self.take("OP")["value"])
                    operands.append(self.bit_or())
                result = node("compare_chain", operator, operands=operands, operators=operators)
        return result

    def binary_level(self,next_level,operators):
        result=next_level()
        while self.current["value"] in operators:
            token=self.take("OP");result=node(operators[token["value"]],token,left=result,right=next_level())
        return result

    def bit_or(self):return self.binary_level(self.bit_xor,{"|":"bit_or"})
    def bit_xor(self):return self.binary_level(self.bit_and,{"^":"bit_xor"})
    def bit_and(self):return self.binary_level(self.shift,{"&":"bit_and"})
    def shift(self):return self.binary_level(self.sum,{"<<":"shift_left",">>":"shift_right"})

    def sum(self):
        result = self.term()
        # + and - have equal precedence and associate left to right.
        while self.current["kind"] == "OP" and self.current["value"] in {"+", "-"}:
            operator = self.take("OP")
            result = node("add" if operator["value"] == "+" else "subtract", operator,
                          left=result, right=self.term())
        return result

    def term(self):
        result = self.unary()
        while self.current["value"] in {"%", "*", "//", "/"}:
            operator = self.take("OP")
            result = node({"%": "modulo", "*": "multiply", "//": "floor_divide", "/":"true_divide"}[operator["value"]], operator, left=result, right=self.unary())
        return result

    def unary(self):
        if self.current["value"] in {"+","-","~"}:
            token=self.take("OP");value=self.unary()
            return value if token["value"]=="+" else node("negate" if token["value"]=="-" else "invert",token,value=value)
        value=self.atom()
        if self.current["value"]=="**":
            token=self.take("OP");value=node("power",token,left=value,right=self.unary())
        return value

    def atom(self):
        token = self.current
        if token["kind"]=="NONE":self.take("NONE");return node("none",token)
        if token["kind"]=="FSTRING":
            import ast
            from .lexer import tokenize
            self.take("FSTRING")
            try:joined=ast.parse(token["value"],mode="eval").body
            except SyntaxError:raise CompileError("Invalid f-string",token) from None
            values=[]
            for value in joined.values:
                if isinstance(value,ast.Constant):values.append(node("string",token,value=value.value))
                else:
                    expression=Parser(tokenize(ast.unparse(value.value))).expression()
                    def locate(n):
                        if isinstance(n,dict):
                            n.update(line=token["line"],column=token["column"])
                            if "filename" in token:n["filename"]=token["filename"]
                            for child in list(n.values()):locate(child)
                        elif isinstance(n,list):
                            for child in n:locate(child)
                    locate(expression)
                    spec=""
                    if value.format_spec:
                        if any(not isinstance(v,ast.Constant) for v in value.format_spec.values):raise CompileError("Format width must be constant",token)
                        spec="".join(v.value for v in value.format_spec.values)
                    values.append(node("format_value",token,value=expression,conversion=value.conversion,spec=spec))
            return self.postfix(node("fstring",token,values=values))
        if token["kind"] == "STRING":
            import ast
            self.take("STRING")
            try:
                value = ast.literal_eval(token["value"])
            except (ValueError, SyntaxError):
                raise CompileError("Invalid string escape", token) from None
            if any(not 0 <= ord(c) <= 127 for c in value):
                raise CompileError("Text supports printable ASCII and newline", token)
            return self.postfix(node("string", token, value=value))
        if token["kind"] == "DECIMAL":
            self.take("DECIMAL")
            return node("decimal", token, value=token["value"])
        if token["kind"] == "TYPE" and token["value"] in {"int", "i32", "float", "dict", "str", "tuple", "set"}:
            self.take("TYPE")
            return self.postfix(self.call(token))
        if token["value"] == "{":
            self.take("OP", "{")
            entries = []
            if self.current["value"] != "}":
                while True:
                    key = self.expression()
                    if not entries and self.current["kind"]=="FOR":
                        clauses=self.comprehension_clauses()
                        self.take("OP","}")
                        return self.postfix(node("comprehension",token,value=key,collection="set",clauses=clauses))
                    if not entries and self.current["value"]!=":":
                        values=[key]
                        while self.current["value"]==",":
                            self.take("OP",",")
                            if self.current["value"]=="}":break
                            values.append(self.expression())
                        self.take("OP","}");return self.postfix(node("set",token,values=values))
                    self.take("OP", ":")
                    value=self.expression()
                    if not entries and self.current["kind"]=="FOR":
                        clauses=self.comprehension_clauses()
                        self.take("OP","}")
                        return self.postfix(node("comprehension",token,key=key,value=value,collection="dict",clauses=clauses))
                    entries.append({"key": key, "value": value})
                    if self.current["value"] != ",": break
                    self.take("OP", ",")
                    if self.current["value"] == "}": break
            self.take("OP", "}")
            return self.postfix(node("dict", token, entries=entries))
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
                    clauses = self.comprehension_clauses()
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
                index = None if self.current["value"]==":" else self.expression()
                if self.current["value"]==":":
                    self.take("OP",":")
                    stop=None if self.current["value"] in {":","]"} else self.expression()
                    step=None
                    if self.current["value"]==":":
                        self.take("OP",":")
                        if self.current["value"]!="]":step=self.expression()
                    self.take("OP","]")
                    result=node("slice",token,base=result,start=index,stop=stop,step=step)
                    continue
                self.take("OP", "]")
                result = node("index", token, base=result, index=index)
            else:
                self.take("OP", ".")
                method = self.take("NAME")
                if self.current["value"]=="(":
                    call = self.call(method)
                    result = node("method", token, base=result, name=method["value"], arguments=call["arguments"])
                else:result=node("attribute",token,base=result,name=method["value"])
        return result

    def comprehension_clauses(self):
        clauses=[]
        while self.current["kind"]=="FOR":
            start=self.take("FOR");names=self.targets();self.take("IN")
            iterable=self.expression(False);filters=[]
            while self.current["kind"]=="IF":
                self.take("IF");filters.append(self.expression(False))
            clauses.append(node("for",start,name=names[0],names=names,iterable=iterable,filters=filters,body=[]))
        return clauses

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
                if self.current["kind"]=="NAME" and self.tokens[self.index+1]["value"]=="=":
                    key=self.take("NAME");self.take("OP","=")
                    arguments.append(node("keyword",key,name=key["value"],value=self.expression()))
                else:arguments.append(self.expression())
                if self.current["value"] != ",":
                    break
                self.take("OP", ",")
        self.take("OP", ")")
        return node("call", name, name=name["value"], arguments=arguments)


def parse(tokens):
    return Parser(tokens).program()
