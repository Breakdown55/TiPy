"""Resolve dictionary nodes, check types/flow, and lay out per-call storage."""

from copy import deepcopy
from .errors import CompileError

PIXEL_BUILTINS = {"flip_pixel", "pixel_on", "pixel_off"}
GRAPHICS_BUILTINS = PIXEL_BUILTINS | {"clear", "render", "v_line", "h_line", "line", "rectangle"}
PYTHON_BUILTINS = {"range", "list", "abs", "sum", "min", "max", "any", "all", "enumerate", "zip"}


def check(tree):
    checked = deepcopy(tree)
    from .comprehensions import prepare
    prepare(checked)
    globals_ = {}
    functions = {}
    global_calls = []
    context = {"locals": globals_, "initialized": set(), "function": None}
    checked["temporaries"] = {}

    def normalize(name):
        if name.endswith("[]"):
            return normalize(name[:-2]) + "[]"
        return "i16" if name == "int" else name

    # Signatures are collected before bodies, allowing forward and recursive calls.
    for statement in checked["body"]:
        if statement["kind"] != "function":
            continue
        name = statement["name"]
        if name == "len":
            raise CompileError("len is a reserved array builtin", statement)
        if name in GRAPHICS_BUILTINS - {"clear"}:
            raise CompileError(f"{name} is a reserved graphics builtin", statement)
        if name in functions:
            raise CompileError(f"Function {name!r} is already defined", statement)
        if statement["return_type"] == "auto":
            def valued_return(value):
                if isinstance(value, dict):
                    return value.get("kind") == "return" and value.get("value") is not None or any(valued_return(v) for v in value.values())
                return isinstance(value, list) and any(valued_return(v) for v in value)
            statement["return_type"] = "i16" if valued_return(statement["body"]) else "void"
        statement["return_type"] = normalize(statement["return_type"])
        if statement["return_type"] == "void[]":
            raise CompileError("An array cannot have void elements", statement)
        statement["label"] = f"__fn_{len(functions)}"
        statement["requires_globals"] = set()
        statement["calls"] = set()
        if len(statement["params"]) > 62:
            raise CompileError("At most 62 parameters fit the current stack frame layout", statement)
        names = set()
        for parameter in statement["params"]:
            parameter["type"] = normalize(parameter["type"])
            if parameter["type"] in {"void", "void[]"}:
                raise CompileError("A parameter cannot be void", parameter)
            if parameter["name"] in names:
                raise CompileError(f"Duplicate parameter {parameter['name']!r}", parameter)
            names.add(parameter["name"])
        functions[name] = statement

    def bind(item, symbol):
        item["symbol"] = symbol["label"]
        item["storage"] = symbol.get("storage", "global")
        if "offset" in symbol:
            item["offset"] = symbol["offset"]

    def lookup(name, item, writing=False):
        if name in context["locals"]:
            if name not in context["initialized"]:
                raise CompileError(f"Variable {name!r} may not be initialized on this branch", item)
            return context["locals"][name]
        if context["function"] and name in globals_:
            if writing:
                raise CompileError("Declare a local before assigning inside a function; global writes are not supported yet", item)
            context["function"]["requires_globals"].add(name)
            return globals_[name]
        raise CompileError(f"Variable {name!r} has not been declared", item)

    def compatible(target, source_type, constant, item):
        if (target == "fixed") != (source_type == "fixed"):
            raise CompileError("Use int(...) to convert fixed-point values to integers", item)
        if target.endswith("[]") or source_type.endswith("[]"):
            if target != source_type:
                raise CompileError(f"Cannot assign {source_type} to {target}", item)
            return
        if source_type == "void":
            raise CompileError("A void function does not produce a value", item)
        if (target == "bool") != (source_type == "bool"):
            raise CompileError(f"Cannot assign {source_type} to {target}", item)
        if target == "bool":
            return
        if constant is not None:
            low, high = (0, 255) if target == "u8" else (-32768, 32767)
            if not low <= constant <= high:
                raise CompileError(f"{constant} is outside {target} range {low} to {high}", item)
        elif target == "u8" and source_type != "u8":
            raise CompileError("Cannot narrow an i16 expression to u8 without a proven range", item)

    def expression(item, allow_min_magnitude=False, allow_void=False, expected_element=None):
        kind = item["kind"]
        constant = None
        if kind == "number":
            value_type, constant = "i16", item["value"]
            if constant > (32768 if allow_min_magnitude else 32767):
                raise CompileError(f"{constant} is outside i16 literal range", item)
        elif kind == "decimal":
            from decimal import Decimal, ROUND_HALF_UP
            raw = int((Decimal(str(item["value"])) * 256).to_integral_value(rounding=ROUND_HALF_UP))
            if not -32768 <= raw <= (32768 if allow_min_magnitude else 32767):
                raise CompileError("Fixed-point literal must be between -128 and 127.996", item)
            item["raw"] = raw
            value_type = "fixed"
        elif kind == "stdlib":
            name, args = item["name"], item["arguments"]
            count = 2 if name == "random.randint" else 1
            if len(args) != count:
                raise CompileError(f"{name} expects {count} arguments", item)
            types = [expression(arg)[0] for arg in args]
            if name in {"random.randint", "random.seed", "math.radians"} and any(t not in {"i16", "u8"} for t in types):
                raise CompileError(f"{name} requires integer arguments", item)
            if name in {"math.sin", "math.cos", "time.sleep"} and any(t not in {"i16", "u8", "fixed"} for t in types):
                raise CompileError(f"{name} requires numeric arguments", item)
            if name == "time.sleep":
                # Literal seconds retain their decimal precision for delays.
                from decimal import Decimal
                arg = args[0]
                sign = -1 if arg["kind"] == "negate" else 1
                literal = arg["value"] if sign == -1 else arg
                if isinstance(literal, dict) and literal["kind"] in {"number", "decimal"}:
                    milliseconds = int(Decimal(str(literal["value"])) * 1000) * sign
                    if not 0 <= milliseconds <= 32767:
                        raise CompileError("sleep must be between 0 and 32.767 seconds", item)
                    item["milliseconds"] = milliseconds
                value_type = "void"
            else:
                value_type = "fixed" if name.startswith("math.") else "void" if name == "random.seed" else "i16"
        elif kind == "call" and item["name"] == "int":
            if len(item["arguments"]) != 1:
                raise CompileError("int expects one numeric value", item)
            source_type, constant = expression(item["arguments"][0])
            if source_type not in {"i16", "u8", "bool", "fixed"}:
                raise CompileError("int expects a scalar numeric value", item)
            item["kind"] = "cast_int"
            value_type = "i16"
        elif kind == "compare_chain":
            types = [expression(arg)[0] for arg in item["operands"]]
            if any(t not in {"i16", "u8"} for t in types):
                raise CompileError("Chained comparisons currently require integers", item)
            value_type = "bool"
        elif kind == "boolean":
            value_type, constant = "bool", item["value"]
        elif kind == "tuple":
            raise CompileError("Tuple values currently require an unpacked for loop over a literal list", item)
        elif kind == "name":
            symbol = lookup(item["name"], item)
            bind(item, symbol)
            value_type = symbol["type"]
        elif kind == "comprehension":
            before = context["initialized"].copy()
            item["collect"]["expected_element"] = expected_element
            statements([item["loop"]])
            context["initialized"] = before
            value_type = item["collect"]["type"] + "[]"
            item["owned"] = True
        elif kind == "list":
            element = expected_element
            if element == "void" or element and element.endswith("[]"):
                raise CompileError("Arrays need a scalar element type", item)
            for value in item["values"]:
                value_type, value_constant = expression(value)
                if element is None:
                    element = "bool" if value_type == "bool" else "i16"
                compatible(element, value_type, value_constant, value)
            value_type = (element or "i16") + "[]"
            item["owned"] = True
        elif kind in {"index", "method"}:
            array_type, _ = expression(item["base"], expected_element=expected_element)
            if not array_type.endswith("[]"):
                raise CompileError("Indexing and list methods require an array", item)
            element = array_type[:-2]
            if kind == "index":
                index_type, _ = expression(item["index"])
                if index_type not in {"i16", "u8"}:
                    raise CompileError("An array index must be an integer", item["index"])
                value_type = element
            else:
                method = item["name"]
                counts = {"append": (1,), "insert": (2,), "pop": (0, 1), "remove": (1,),
                          "clear": (0,), "extend": (1,), "reverse": (0,), "copy": (0,),
                          "count": (1,), "index": (1,)}
                if method not in counts or len(item["arguments"]) not in counts[method]:
                    raise CompileError(f"Unsupported array method or argument count: {method}", item)
                for index, argument in enumerate(item["arguments"]):
                    argument_type, value = expression(argument, expected_element=element if method == "extend" else None)
                    if method == "extend":
                        compatible(array_type, argument_type, value, argument)
                    elif method in {"insert", "pop"} and index == 0:
                        if argument_type not in {"i16", "u8"}:
                            raise CompileError("An array index must be an integer", argument)
                    else:
                        compatible(element, argument_type, value, argument)
                value_type = element if method == "pop" else "i16" if method in {"count", "index"} else array_type if method == "copy" else "void"
                if method == "copy":
                    item["owned"] = True
        elif kind == "call" and item["name"] in PYTHON_BUILTINS and item["name"] not in functions:
            name, arguments = item["name"], item["arguments"]
            if name in context["locals"] or name in globals_:
                raise CompileError(f"Variable {name!r} is not callable", item)
            count = len(arguments)
            if name in {"enumerate", "zip"}:
                raise CompileError(f"{name} currently requires a for loop with unpacked targets", item)
            valid = count in {1, 2, 3} if name == "range" else count in {0, 1} if name == "list" else count in {1, 2} if name == "sum" else count >= 1 if name in {"min", "max"} else count == 1
            if not valid:
                raise CompileError(f"Invalid number of arguments to {name}", item)
            types, constants = [], []
            for argument in arguments:
                t, c = expression(argument, expected_element=expected_element if name == "list" else None)
                types.append(t); constants.append(c)
            if name == "range":
                if any(t not in {"i16", "u8"} for t in types):
                    raise CompileError("range arguments must be integers", item)
                if count == 3 and constants[2] == 0:
                    raise CompileError("range step cannot be zero", item)
                value_type = "i16[]"
            elif name == "list":
                if count and not types[0].endswith("[]"):
                    raise CompileError("list expects an array or range", item)
                value_type = types[0] if count else (expected_element or "i16") + "[]"
            elif name == "abs":
                if types[0] not in {"i16", "u8"}:
                    raise CompileError("abs expects an integer", item)
                value_type = "i16"
                constant = abs(constants[0]) if constants[0] is not None else None
            elif count == 1 or name in {"sum", "any", "all"}:
                if not types[0].endswith("[]"):
                    raise CompileError(f"{name} expects an array or range", item)
                if name == "sum" and count == 2 and types[1] not in {"i16", "u8"}:
                    raise CompileError("sum start must be an integer", item)
                value_type = "bool" if name in {"any", "all"} else "i16" if name == "sum" else types[0][:-2]
            else:
                if any(t not in {"i16", "u8"} for t in types):
                    raise CompileError(f"{name} scalar arguments must be integers", item)
                value_type = "i16"
                item["scalar"] = True
            item["kind"] = "builtin"
            if value_type.endswith("[]"):
                item["owned"] = True
        elif kind == "call" and item["name"] in GRAPHICS_BUILTINS and item["name"] not in functions:
            name = item["name"]
            count = 2 if name in PIXEL_BUILTINS else 0 if name in {"clear", "render"} else 6 if name == "rectangle" else 4
            if len(item["arguments"]) != count:
                detail = "two coordinates (x, y)" if count == 2 else f"{count} arguments"
                raise CompileError(f"{name} expects {detail}", item)
            for index, argument in enumerate(item["arguments"]):
                argument_type, _ = expression(argument)
                if name == "rectangle" and index == 5:
                    if argument_type != "bool":
                        raise CompileError("Rectangle filled must be bool", argument)
                    continue
                if argument_type not in {"i16", "u8"}:
                    raise CompileError("Graphics coordinates and width must be integers", argument)
            item["kind"] = "graphics"
            value_type = "void"
        elif kind == "call" and item["name"] == "len":
            if len(item["arguments"]) != 1:
                raise CompileError("len expects one array", item)
            argument_type, _ = expression(item["arguments"][0])
            if not argument_type.endswith("[]"):
                raise CompileError("len expects an array", item)
            item["kind"] = "length"
            value_type = "i16"
        elif kind == "call":
            function = functions.get(item["name"])
            if function is None:
                raise CompileError(f"Function {item['name']!r} has not been defined", item)
            if len(item["arguments"]) != len(function["params"]):
                raise CompileError(f"{item['name']} expects {len(function['params'])} arguments, got {len(item['arguments'])}", item)
            for argument, parameter in zip(item["arguments"], function["params"]):
                argument_type, value = expression(argument, expected_element=parameter["type"][:-2] if parameter["type"].endswith("[]") else None)
                compatible(parameter["type"], argument_type, value, argument)
            item["symbol"] = function["label"]
            value_type = function["return_type"]
            if value_type.endswith("[]"):
                item["owned"] = True
            if context["function"]:
                context["function"]["calls"].add(item["name"])
            else:
                global_calls.append((item, context["initialized"].copy()))
        elif kind == "membership":
            left_type, left_constant = expression(item["left"])
            right_type, _ = expression(item["right"], expected_element="bool" if left_type == "bool" else None)
            if not right_type.endswith("[]"):
                raise CompileError("in requires an array or range on the right", item)
            compatible(right_type[:-2], left_type, left_constant, item)
            value_type = "bool"
        elif kind == "not":
            operand_type, operand = expression(item["value"])
            if operand_type != "bool":
                raise CompileError("not requires bool", item)
            value_type, constant = "bool", not operand if operand is not None else None
        elif kind == "negate":
            operand_type, operand = expression(item["value"], allow_min_magnitude=True)
            if operand_type == "bool" or operand_type.endswith("[]"):
                raise CompileError("Cannot negate a bool or array", item)
            value_type = operand_type if operand_type == "fixed" else "i16"
            constant = -operand if operand is not None else None
        else:
            left_type, left = expression(item["left"])
            right_type, right = expression(item["right"])
            if "fixed" in {left_type, right_type}:
                if kind not in {"add", "subtract", "multiply", "compare"} or any(t not in {"fixed", "i16", "u8"} for t in (left_type, right_type)):
                    raise CompileError("Unsupported fixed-point operation", item)
                if kind == "multiply" and left_type == right_type == "fixed":
                    raise CompileError("Multiplication of two fixed-point values is not supported yet", item)
                if kind != "multiply":
                    for field, t in (("left", left_type), ("right", right_type)):
                        if t != "fixed": item[field] = {"kind": "to_fixed", "value": item[field], "type": "fixed"}
                item["type"] = "bool" if kind == "compare" else "fixed"
                return item["type"], None
            if left_type.endswith("[]") or right_type.endswith("[]"):
                raise CompileError("Use indexing or array methods instead of scalar operators on arrays", item)
            if kind in {"and", "or"}:
                if left_type != "bool" or right_type != "bool":
                    raise CompileError("and/or require bool operands", item)
                value_type = "bool"
            elif kind == "compare":
                if (left_type == "bool") != (right_type == "bool"):
                    raise CompileError("Cannot compare bool with an integer", item)
                op = item["operator"]
                if left_type == "bool" and op not in {"==", "!="}:
                    raise CompileError("Bool values only support == and != comparisons", item)
                value_type = "bool"
                if left is not None and right is not None:
                    constant = {"==": left == right, "!=": left != right, "<": left < right,
                                ">": left > right, "<=": left <= right, ">=": left >= right}[op]
            else:
                if "bool" in {left_type, right_type}:
                    raise CompileError("Addition, subtraction and remainder require integers", item)
                value_type = "i16"
                if kind in {"modulo", "floor_divide"} and right == 0:
                    raise CompileError("Remainder divisor cannot be zero" if kind == "modulo" else "Division divisor cannot be zero", item)
                if left is not None and right is not None:
                    constant = {"modulo": lambda: left % right, "floor_divide": lambda: left // right,
                                "multiply": lambda: left * right, "add": lambda: left + right,
                                "subtract": lambda: left - right}[kind]()
        if value_type == "void" and not allow_void:
            raise CompileError("A void function does not produce a value", item)
        item["type"] = value_type
        if value_type == "i16" and constant is not None and kind != "number" and not -32768 <= constant <= 32767:
            raise CompileError(f"Constant result {constant} is outside i16 range", item)
        return value_type, constant

    def statements(body, loop_depth=0, top_level=False):
        reachable = True
        for statement in body:
            kind = statement["kind"]
            if kind == "function":
                if not top_level:
                    raise CompileError("Function definitions must be at the top level", statement)
                continue
            if not reachable:
                statement["unreachable"] = True
                continue
            if kind == "pass":
                continue
            if kind == "expression_statement":
                expression(statement["value"], allow_void=True)
                continue
            if kind == "collect":
                element, constant = expression(statement["value"])
                if element.endswith("[]") or element == "void":
                    raise CompileError("Comprehensions require scalar elements", statement)
                expected = statement.get("expected_element")
                if expected:
                    compatible(expected, element, constant, statement)
                    element = expected
                statement["type"] = element
                continue
            if kind == "index_assign":
                target_type, _ = expression(statement["target"])
                source_type, value = expression(statement["value"])
                if statement["operator"] != "=":
                    if target_type not in {"i16", "u8"} or source_type not in {"i16", "u8"}:
                        raise CompileError("Array arithmetic requires integers", statement)
                    source_type, value = "i16", None
                compatible(target_type, source_type, value, statement)
                statement["type"] = target_type
                continue
            if kind == "return":
                function = context["function"]
                if function is None:
                    raise CompileError("return must be inside a function", statement)
                target = function["return_type"]
                if target == "void":
                    if statement["value"] is not None:
                        raise CompileError("A void function cannot return a value", statement)
                else:
                    if statement["value"] is None:
                        raise CompileError("This function must return a value", statement)
                    value_type, value = expression(statement["value"], expected_element=target[:-2] if target.endswith("[]") else None)
                    compatible(target, value_type, value, statement)
                reachable = False
                continue
            if kind in {"break", "continue"}:
                if loop_depth == 0:
                    raise CompileError(f"{kind} must be inside a while loop", statement)
                reachable = False
                continue
            if kind == "for":
                iterable = statement["iterable"]
                names = statement.get("names", [statement["name"]])
                if iterable["kind"] == "list" and (any(v["kind"] == "tuple" for v in iterable["values"]) or not iterable["values"] and len(names) > 1):
                    # Scalar rows have a known shape; no Python tuple objects or
                    # heap descriptors are needed for this iteration.
                    rows, constants, elements = iterable["values"], [], [None] * len(names)
                    for row in rows:
                        if row["kind"] != "tuple" or len(row["values"]) != len(names):
                            raise CompileError(f"Every tuple row must have {len(names)} values to unpack", row)
                        values = []
                        for index, value in enumerate(row["values"]):
                            value_type, constant = expression(value)
                            if value_type not in {"i16", "u8", "bool", "fixed"}:
                                raise CompileError("Tuple loop components must be scalar values", value)
                            if elements[index] is None:
                                elements[index] = value_type if value_type in {"bool", "fixed"} else "i16"
                            compatible(elements[index], value_type, constant, value)
                            values.append(constant)
                        row["type"] = "tuple"
                        constants.append(values)
                    elements = [t or "i16" for t in elements]
                    before = context["initialized"].copy()
                    targets = []
                    for name, element in zip(names, elements):
                        if name in functions or name in GRAPHICS_BUILTINS or name == "len":
                            raise CompileError("Loop variable conflicts with a function name", statement)
                        target = context["locals"].get(name)
                        if target is None:
                            target = {"type": element, "size": 1 if element == "bool" else 2, "label": f"__var_{len(globals_)}"}
                            globals_[name] = target
                            if name.startswith("$comp_"): checked["temporaries"][target["label"]] = target
                        if target.pop("loop_inferred", False): target["type"] = element
                        compatible(target["type"], element, None, statement)
                        targets.append({**target, "symbol": target["label"]})
                        context["initialized"].add(name)
                    for field in ("iterator_0", "iteration_index"):
                        if context["function"]:
                            symbol = statement[field]
                            symbol["type"] = "i16"
                        else:
                            symbol = {"type": "i16", "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                            checked["temporaries"][symbol["label"]] = symbol
                        statement[field] = {**symbol, "symbol": symbol["label"]}
                    if all(value is not None for row in constants for value in row):
                        statement["constant_rows"] = constants
                    iterable.update(kind="tuple_sequence", type="tuple_sequence")
                    statement.update(iteration_kind="tuple", targets=targets, target=targets[-1], sources=[iterable])
                    statements(statement["body"], loop_depth + 1)
                    context["initialized"] = before
                    continue
                if iterable["kind"] == "call" and iterable.get("name") == "range" and "range" not in functions:
                    expression(iterable)
                    names = statement.get("names", [statement["name"]])
                    if len(names) != 1:
                        raise CompileError("range requires one target name", statement)
                    name = names[0]
                    if name in functions or name in GRAPHICS_BUILTINS or name == "len":
                        raise CompileError("Loop variable conflicts with a function name", statement)
                    before = context["initialized"].copy()
                    fields = {}
                    for field in ("range_current", "range_stop", "range_step"):
                        if context["function"]:
                            symbol = statement[field]
                        else:
                            symbol = {"type": "i16", "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                            checked["temporaries"][symbol["label"]] = symbol
                        fields[field] = {**symbol, "symbol": symbol["label"]}
                    target = context["locals"].get(name)
                    if target is None:
                        target = {"type": "i16", "size": 2, "label": f"__var_{len(globals_)}"}
                        globals_[name] = target
                        if name.startswith("$comp_"):
                            checked["temporaries"][target["label"]] = target
                    if target.pop("loop_inferred", False):
                        target["type"] = "i16"
                    compatible(target["type"], "i16", None, statement)
                    statement.update(fields)
                    statement["iteration_kind"] = "range"
                    statement["target"] = {**target, "symbol": target["label"]}
                    context["initialized"].add(name)
                    statements(statement["body"], loop_depth + 1)
                    context["initialized"] = before
                    continue
                mode = iterable.get("name") if iterable["kind"] == "call" and iterable.get("name") in {"enumerate", "zip"} and iterable.get("name") not in functions else "array"
                sources = iterable["arguments"] if mode == "zip" else iterable["arguments"][:1] if mode == "enumerate" else [iterable]
                if mode == "zip" and not sources or mode == "enumerate" and len(iterable["arguments"]) not in {1, 2}:
                    raise CompileError(f"Invalid arguments to {mode}", iterable)
                types = []
                for source in sources:
                    iterable_type, _ = expression(source)
                    if not iterable_type.endswith("[]"):
                        raise CompileError("A for loop requires an array or range", source)
                    types.append(iterable_type)
                elements = [t[:-2] for t in types]
                if mode == "enumerate":
                    start = iterable["arguments"][1] if len(iterable["arguments"]) == 2 else {"kind": "number", "value": 0, "line": statement["line"], "column": statement["column"]}
                    if expression(start)[0] not in {"i16", "u8"}:
                        raise CompileError("enumerate start must be an integer", start)
                    statement["enumeration_start"] = start
                    elements.insert(0, "i16")
                names = statement.get("names", [statement["name"]])
                if len(names) != len(elements):
                    raise CompileError(f"This iteration requires {len(elements)} target name(s)", statement)
                before = context["initialized"].copy()
                def slot(field, type_name):
                    if context["function"]:
                        symbol = statement[field]
                        symbol["type"] = type_name
                    else:
                        symbol = {"type": type_name, "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                        checked["temporaries"][symbol["label"]] = symbol
                    return {**symbol, "symbol": symbol["label"]}
                statement["iterators"] = [slot(f"iterator_{i}", t) for i, t in enumerate(types)]
                statement["iteration_index"] = slot("iteration_index", "i16")
                if mode == "enumerate":
                    statement["enumeration"] = slot("enumeration", "i16")
                statement["sources"], statement["iteration_kind"] = sources, mode
                targets = []
                for name, element in zip(names, elements):
                    if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                        raise CompileError("Loop variable conflicts with a function name", statement)
                    target = context["locals"].get(name)
                    if target is None:
                        target = {"type": element, "size": 1 if element in {"u8", "bool"} else 2,
                                  "label": f"__var_{len(globals_)}"}
                        globals_[name] = target
                        if name.startswith("$comp_"):
                            checked["temporaries"][target["label"]] = target
                    if target.pop("loop_inferred", False):
                        target["type"] = element
                    compatible(target["type"], element, None, statement)
                    targets.append({**target, "symbol": target["label"]})
                    context["initialized"].add(name)
                statement["targets"] = targets
                statement["iterator"], statement["target"] = statement["iterators"][0], targets[-1]
                statements(statement["body"], loop_depth + 1)
                context["initialized"] = before
                continue
            if kind in {"if", "while"}:
                condition_type, _ = expression(statement["condition"])
                if condition_type != "bool":
                    raise CompileError(f"{'An if' if kind == 'if' else 'A while'} condition must be bool", statement["condition"])
                before = context["initialized"].copy()
                then_falls = statements(statement["body"], loop_depth + (kind == "while"))
                then_initialized = context["initialized"].copy()
                context["initialized"] = before.copy()
                if kind == "while":
                    continue
                else_falls = statements(statement["alternative"], loop_depth)
                if then_falls and else_falls:
                    context["initialized"] &= then_initialized
                elif then_falls:
                    context["initialized"] = then_initialized
                reachable = then_falls or else_falls
                continue
            name = statement["name"]
            if kind == "assign" and statement.get("inferred"):
                if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                    raise CompileError(f"{name!r} is already a function name", statement)
                symbol = context["locals"].get(name)
                source_type, constant = expression(statement["value"])
                if symbol is None:
                    symbol = {"type": source_type, "size": 1 if source_type in {"u8", "bool"} else 2,
                              "label": f"__var_{len(globals_)}"}
                    context["locals"][name] = symbol
                    statement["kind"] = "declare"
                elif symbol["type"] == "auto":
                    symbol["type"] = source_type
                    statement["kind"] = "declare"
                else:
                    compatible(symbol["type"], source_type, constant, statement)
                context["initialized"].add(name)
                statement["type"] = symbol["type"]
                bind(statement, symbol)
                continue
            if kind == "declare":
                target = normalize(statement["type"])
                if target == "void":
                    raise CompileError("A variable cannot be void", statement)
                if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                    raise CompileError(f"{name!r} is already a function name", statement)
                if context["function"]:
                    symbol = context["locals"][name]
                else:
                    if name in globals_:
                        raise CompileError(f"Variable {name!r} is already declared", statement)
                    symbol = {"type": target, "label": f"__var_{len(globals_)}",
                              "size": 1 if target in {"u8", "bool"} else 2}
                source_type, constant = expression(statement["value"], expected_element=target)
                if source_type.endswith("[]"):
                    target += "[]"
                    symbol["type"] = target
                    symbol["size"] = 2
                compatible(target, source_type, constant, statement)
                if not context["function"]:
                    # Comprehensions may allocate private globals while the
                    # initializer is checked. Assign this label afterwards.
                    symbol["label"] = f"__var_{len(globals_)}"
                context["locals"][name] = symbol
                context["initialized"].add(name)
            else:
                symbol = lookup(name, statement, writing=True)
                source_type, constant = expression(statement["value"], expected_element=symbol["type"][:-2] if symbol["type"].endswith("[]") else None)
                compatible(symbol["type"], source_type, constant, statement)
            statement["type"] = symbol["type"]
            bind(statement, symbol)
        return reachable

    statements(checked["body"], top_level=True)

    def allocate_locals(body, local_symbols, size):
        for statement in body:
            def comprehension_slots(item):
                nonlocal size
                if isinstance(item, dict):
                    if item.get("kind") == "comprehension":
                        size = allocate_locals([item["loop"]], local_symbols, size)
                        return
                    for key, value in item.items():
                        if key not in {"body", "alternative", "loop"}:
                            comprehension_slots(value)
                elif isinstance(item, list):
                    for value in item:
                        comprehension_slots(value)
            comprehension_slots(statement)
            kind = statement["kind"]
            if kind == "function":
                raise CompileError("Nested functions are not supported", statement)
            if kind == "declare" or kind == "assign" and statement.get("inferred"):
                name = statement["name"]
                if name in local_symbols:
                    if kind == "assign": continue
                    raise CompileError(f"Variable {name!r} is already declared in this function", statement)
                type_name = normalize(statement.get("type", "auto"))
                # Reserve word slots for locals; an initializer can infer an
                # array descriptor even when its declared element type is u8.
                width = 2
                size += width
                if size > 128:
                    raise CompileError("Function locals exceed the current 128-byte stack frame limit", statement)
                local_symbols[name] = {"type": type_name, "size": width,
                                       "storage": "frame", "offset": -size,
                                       "label": f"__local_{len(local_symbols)}"}
            elif kind == "for":
                iterable = statement["iterable"]
                mode = iterable.get("name") if iterable["kind"] == "call" and iterable.get("name") not in functions else "array"
                count = len(iterable["arguments"]) if mode == "zip" else 1
                fields = [(f"iterator_{i}", "i16[]") for i in range(count)] + [("iteration_index", "i16")]
                if mode == "range":
                    fields = [(field, "i16") for field in ("range_current", "range_stop", "range_step")]
                if mode == "enumerate":
                    fields.append(("enumeration", "i16"))
                for field, type_name in fields:
                    size += 2
                    slot = {"type": type_name, "size": 2, "storage": "frame", "offset": -size,
                            "label": f"__local_{len(local_symbols)}"}
                    local_symbols[f"${field}{len(local_symbols)}"] = slot
                    statement[field] = slot
                for name in statement.get("names", [statement["name"]]):
                    if name not in local_symbols:
                        size += 2
                        local_symbols[name] = {"type": "i16", "size": 2, "storage": "frame", "offset": -size,
                                               "label": f"__local_{len(local_symbols)}", "loop_inferred": True}
                if size > 128:
                    raise CompileError("Function locals exceed the current 128-byte stack frame limit", statement)
                size = allocate_locals(statement["body"], local_symbols, size)
            elif kind in {"if", "while"}:
                size = allocate_locals(statement["body"], local_symbols, size)
                if kind == "if":
                    size = allocate_locals(statement["alternative"], local_symbols, size)
        return size

    for function in functions.values():
        local_symbols = {}
        count = len(function["params"])
        for index, parameter in enumerate(function["params"]):
            if parameter["name"] in functions:
                raise CompileError("Parameter name conflicts with a function", parameter)
            local_symbols[parameter["name"]] = {
                "type": parameter["type"], "size": 2, "storage": "frame",
                "offset": 4 + 2 * (count - index - 1), "label": f"__arg_{index}"}
        function["frame_size"] = allocate_locals(function["body"], local_symbols, 0)
        function["locals"] = local_symbols
        context = {"locals": local_symbols, "initialized": {p["name"] for p in function["params"]},
                   "function": function}
        falls_through = statements(function["body"])
        if function["return_type"] != "void" and falls_through:
            raise CompileError(f"Function {function['name']!r} must return a value on every path", function)

    # Transitive reads ensure that a call cannot observe a global before its
    # initializer has run, including through mutually recursive functions.
    changed = True
    while changed:
        changed = False
        for function in functions.values():
            before = len(function["requires_globals"])
            for callee in function["calls"]:
                function["requires_globals"].update(functions[callee]["requires_globals"])
            changed |= before != len(function["requires_globals"])
    for call, available in global_calls:
        missing = functions[call["name"]]["requires_globals"] - available
        if missing:
            raise CompileError(f"Call may read uninitialized global {sorted(missing)[0]!r}", call)
    for function in functions.values():
        function["requires_globals"] = sorted(function["requires_globals"])
        function["calls"] = sorted(function["calls"])
    return checked, {name: value for name, value in globals_.items() if not name.startswith("$comp_")}
