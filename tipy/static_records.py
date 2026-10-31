"""Scalar replacement of non-escaping module-level records.

Ordinary TiPy classes remain the public interface. When an instance only has
scalar fields and direct method calls, its fields can occupy static slots and
small void methods can be inlined. Escaping objects retain the normal heap ABI.
This optimization knows nothing about cameras, players, or game behavior.
"""

from copy import deepcopy
from .errors import CompileError


def walk(value, parent=None, key=None):
    if isinstance(value, dict):
        yield value, parent, key
        for name, child in value.items():
            yield from walk(child, value, name)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, parent, key)


def location(node):
    return {key: node[key] for key in ("line", "column", "filename") if key in node}


def fold(node):
    if isinstance(node, list):
        return [fold(value) for value in node]
    if not isinstance(node, dict):
        return node
    result = {
        key: fold(value) if isinstance(value, (dict, list)) else value
        for key, value in node.items()
    }
    if result.get("kind") == "compare" and all(
        result.get(key, {}).get("kind") in {"number", "boolean"}
        for key in ("left", "right")
    ):
        a, b = result["left"]["value"], result["right"]["value"]
        comparisons = {
            "==": a == b,
            "!=": a != b,
            "<": a < b,
            "<=": a <= b,
            ">": a > b,
            ">=": a >= b,
        }
        return {
            "kind": "boolean",
            "value": comparisons[result["operator"]],
            **location(node),
        }
    if result.get("kind") == "negate" and result["value"].get("kind") == "number":
        return {"kind": "number", "value": -result["value"]["value"], **location(node)}
    operations = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "floor_divide": lambda a, b: a // b,
        "modulo": lambda a, b: a % b,
    }
    if result.get("kind") in operations and all(
        result.get(key, {}).get("kind") == "number" for key in ("left", "right")
    ):
        try:
            value = operations[result["kind"]](
                result["left"]["value"], result["right"]["value"]
            )
            if -32768 <= value <= 32767:
                return {"kind": "number", "value": value, **location(node)}
        except ZeroDivisionError:
            pass
    return result


def prepare(tree):
    classes = {}
    returns = {
        node["name"]: node["return_type"]
        for node in tree["body"]
        if node.get("kind") == "function"
    }

    def scalar_expression(value, receiver, fields, methods):
        for node, parent, key in walk(value):
            kind = node.get("kind")
            if kind in {
                "list",
                "dict",
                "tuple",
                "lambda",
                "comprehension",
                "string",
                "none",
            }:
                return False
            if kind == "stdlib" and node.get("name") not in {
                "time.frame_ticks",
                "random.randint",
            }:
                return False
            if (
                kind == "call"
                and node["name"] not in {"int", "bool", "abs", "min", "max", "len"}
                and returns.get(node["name"]) not in {"int", "i16", "u8", "bool"}
            ):
                return False
            if kind == "method" and (
                node.get("base", {}).get("name") != receiver
                or methods.get(node["name"], {}).get("return_type")
                not in {"int", "i16", "u8", "bool"}
            ):
                return False
            if kind == "attribute" and (
                node.get("base", {}).get("name") != receiver
                or node["name"] not in fields
            ):
                return False
        return True

    for item in tree["body"]:
        if item.get("kind") != "class" or item["bases"]:
            continue
        methods = {
            method["name"]: method
            for method in item["body"]
            if method.get("kind") == "function"
        }
        init = methods.get("__init__")
        if not init or not init["params"]:
            continue
        receiver = init["params"][0]["name"]
        fields = []
        for statement in init["body"]:
            target = statement.get("target", {})
            if statement.get("kind") == "pass":
                continue
            if (
                statement.get("kind") != "attribute_assign"
                or statement["operator"] != "="
                or target.get("base", {}).get("name") != receiver
            ):
                break
            if any(
                node.get("kind")
                in {
                    "list",
                    "dict",
                    "tuple",
                    "lambda",
                    "comprehension",
                    "string",
                }
                for node, _, _ in walk(statement["value"])
            ):
                break
            if any(
                node.get("kind") == "method"
                or node.get("kind") == "attribute"
                and node.get("base", {}).get("name") == receiver
                and node["name"] not in fields
                for node, _, _ in walk(statement["value"])
            ):
                break
            fields.append(target["name"])
        else:
            if len(fields) != len(set(fields)):
                continue
            if any(
                parameter["type"] not in {"int", "i16", "bool"}
                for parameter in init["params"][1:]
            ):
                continue
            if any(
                parameter["type"] not in {"int", "i16", "bool"}
                or parameter.get("variadic")
                for method in methods.values()
                for parameter in method["params"][1:]
            ):
                continue
            if any(
                parameter.get("default") is not None
                and any(
                    node.get("kind") not in {"number", "boolean", "negate"}
                    for node, _, _ in walk(parameter["default"])
                )
                for method in methods.values()
                for parameter in method["params"][1:]
            ):
                continue
            if any(
                not scalar_expression(
                    node["value"], method["params"][0]["name"], fields, methods
                )
                or node["target"]["name"] not in fields
                for method in methods.values()
                for node, _, _ in walk(method["body"])
                if node.get("kind") == "attribute_assign"
            ):
                continue
            classes[item["name"]] = (item, methods, fields)

    records = {}
    for declaration in tree["body"]:
        value = declaration.get("value", {})
        if (
            declaration.get("kind") not in {"declare", "assign"}
            or value.get("kind") != "call"
            or value.get("name") not in classes
        ):
            continue
        name = declaration["name"]
        if (
            declaration.get("kind") == "declare"
            and declaration.get("type") != value["name"]
        ):
            continue
        _, methods, fields = classes[value["name"]]
        safe = True
        for node, parent, key in walk(tree["body"]):
            if (
                node.get("kind")
                in {"declare", "assign", "parameter", "for", "target_name"}
                and node.get("name") == name
                and node is not declaration
            ):
                safe = False
            if node.get("kind") == "name" and node.get("name") == name:
                if (
                    parent is None
                    or key != "base"
                    or parent.get("kind") not in {"method", "attribute"}
                ):
                    safe = False
                elif parent["kind"] == "attribute" and parent["name"] not in fields:
                    safe = False
                elif parent["kind"] == "method" and any(
                    argument.get("kind") in {"star_argument", "keyword_unpack"}
                    for argument in parent["arguments"]
                ):
                    safe = False
        if not safe:
            continue
        if any(
            node.get("kind") not in {"keyword", "number", "boolean", "negate"}
            for argument in value["arguments"]
            for node, _, _ in walk(argument)
        ) and not classes[value["name"]][0].get("egx_shape"):
            continue
        # A method may mutate scalar fields, but it must not hand out self.
        for method in methods.values():
            receiver = method["params"][0]["name"]
            for node, parent, key in walk(method["body"]):
                if node.get("kind") in {"class", "function", "lambda", "yield"}:
                    safe = False
                if (
                    node.get("kind") == "name"
                    and node.get("name") == receiver
                    and (
                        key != "base"
                        or parent.get("kind") not in {"method", "attribute"}
                    )
                ):
                    safe = False
                if (
                    node.get("kind") == "attribute"
                    and node.get("base", {}).get("name") == receiver
                    and node["name"] not in fields
                ):
                    safe = False
        if safe:
            records[name] = {
                "declaration": declaration,
                "class": value["name"],
                "methods": methods,
                "fields": fields,
                "constants": {},
                "egx_shape": classes[value["name"]][0].get("egx_shape", False),
                "readonly_fields": classes[value["name"]][0].get("readonly_fields", []),
                "prefix": f"$record_{len(records)}",
            }
    if not records:
        return

    def bind(call, params):
        bindings = {}
        order = []
        keywords = False
        index = 0
        for argument in call["arguments"]:
            if argument.get("kind") == "keyword":
                keywords = True
                name, value = argument["name"], argument["value"]
            else:
                if keywords or index >= len(params):
                    raise CompileError("Invalid record method arguments", call)
                name, value = params[index]["name"], argument
                index += 1
            if name in bindings or name not in {p["name"] for p in params}:
                raise CompileError("Unknown or duplicate argument " + name, argument)
            bindings[name] = deepcopy(value)
            order.append(name)
        for parameter in params:
            if parameter["name"] not in bindings:
                if parameter.get("default") is None:
                    raise CompileError("Missing argument " + parameter["name"], call)
                bindings[parameter["name"]] = deepcopy(parameter["default"])
                order.append(parameter["name"])
        return bindings, order

    specialized = {}

    def prune(items):
        result = []
        for item in items:
            item = fold(item)
            if item.get("kind") == "if" and item["condition"].get("kind") == "boolean":
                result.extend(
                    prune(
                        item["body"]
                        if item["condition"]["value"]
                        else item["alternative"]
                    )
                )
                continue
            for key in ("body", "alternative", "final"):
                if isinstance(item.get(key), list):
                    item[key] = prune(item[key])
            result.append(item)
        return result

    def field(record, name, origin, writing=False):
        if writing and name in record.get("readonly_fields", []):
            raise CompileError(f"{name} is fixed at construction; use scale to resize the object", origin)
        if name not in record["fields"]:
            raise CompileError("Unknown record field " + name, origin)
        if not writing and name in record["constants"]:
            return deepcopy(record["constants"][name])
        return {
            "kind": "name",
            "name": f"{record['prefix']}_{name}",
            "egx_numeric_property": record.get("egx_shape") and name in {"rotation_x", "rotation_y", "rotation_z", "scale"},
            **location(origin),
        }

    def rewrite(value, record=None, receiver=None, substitutions=None):
        if isinstance(value, list):
            return [rewrite(child, record, receiver, substitutions) for child in value]
        if not isinstance(value, dict):
            return value
        kind = value.get("kind")
        if kind == "name" and substitutions and value["name"] in substitutions:
            return deepcopy(substitutions[value["name"]])
        base = value.get("base", {})
        target = value.get("target", {})
        owner = (
            record
            if base.get("kind") == "name" and base.get("name") == receiver
            else records.get(base.get("name")) if base.get("kind") == "name" else None
        )
        if kind == "attribute" and owner:
            return field(owner, value["name"], value)
        if kind == "method" and owner:
            method = owner["methods"].get(value["name"])
            if method is None:
                raise CompileError("Unknown record method " + value["name"], value)
            name = f"{owner['prefix']}_{value['name']}"
            if name not in specialized:
                function = deepcopy(method)
                specialized[name] = function
                function["name"] = name
                function["params"] = function["params"][1:]
                function["body"] = rewrite(
                    method["body"], owner, method["params"][0]["name"]
                )
            return {
                "kind": "call",
                "name": name,
                "arguments": rewrite(
                    value["arguments"], record, receiver, substitutions
                ),
                **location(value),
            }
        target_base = target.get("base", {})
        owner = (
            record
            if target_base.get("kind") == "name" and target_base.get("name") == receiver
            else (
                records.get(target_base.get("name"))
                if target_base.get("kind") == "name"
                else None
            )
        )
        if kind == "attribute_assign" and owner:
            destination = field(owner, target["name"], target, writing=True)
            expression = rewrite(value["value"], record, receiver, substitutions)
            if value["operator"] != "=":
                operators = {
                    "+=": "add",
                    "-=": "subtract",
                    "*=": "multiply",
                    "//=": "floor_divide",
                    "%=": "modulo",
                    "&=": "bit_and",
                    "|=": "bit_or",
                    "^=": "bit_xor",
                    "<<=": "left_shift",
                    ">>=": "right_shift",
                }
                expression = {
                    "kind": operators[value["operator"]],
                    "left": deepcopy(destination),
                    "right": expression,
                    **location(value),
                }
                if value["operator"] == "*=" and destination.get("egx_numeric_property"):
                    expression = {"kind": "call", "name": "__egx_fixed_mul", "arguments": [expression["left"], expression["right"]], **location(value)}
            return {
                "kind": "assign",
                "name": destination["name"],
                "value": expression,
                "static_field": True,
                "egx_numeric_property": destination.get("egx_numeric_property", False),
                **location(value),
            }
        return {
            key: rewrite(child, record, receiver, substitutions)
            for key, child in value.items()
        }

    initializers = {}
    for name, record in records.items():
        init = record["methods"]["__init__"]
        bindings, order = bind(record["declaration"]["value"], init["params"][1:])
        argument_snapshots = []
        parameters = {p['name']:p for p in init['params'][1:]}
        for parameter_name in order:
            value = bindings[parameter_name]
            if record.get('egx_shape') and fold(value).get('kind') not in {'number','boolean'}:
                temp = f"{record['prefix']}_argument_{parameter_name}"
                argument_snapshots.append({'kind':'declare','name':temp,'type':parameters[parameter_name]['type'],'value':deepcopy(value),**location(value)})
                bindings[parameter_name] = {'kind':'name','name':temp,**location(value)}
        for parameter in init["params"][1:]:
            value = bindings[parameter["name"]]
            literal = fold(value)
            if record.get('egx_shape') and literal.get('kind')=='name':continue
            valid = literal.get("kind") == ("boolean" if parameter["type"] == "bool" else "number")
            if not valid or literal.get("kind") == "number" and not -32768 <= literal["value"] <= 32767:
                raise CompileError("Invalid scalar record constructor argument", value)
        writes = {
            node["target"]["name"]
            for method in record["methods"].values()
            if method is not init
            for node, _, _ in walk(method["body"])
            if node.get("kind") == "attribute_assign"
        }
        writes.update(
            node["target"]["name"]
            for node, _, _ in walk(tree["body"])
            if node.get("kind") == "attribute_assign"
            and node["target"].get("base", {}).get("name") == name
        )
        statements = argument_snapshots
        for statement in init["body"]:
            if statement["kind"] == "pass":
                continue
            attribute = statement["target"]["name"]
            expression = fold(
                rewrite(statement["value"], record, init["params"][0]["name"], bindings)
            )
            if attribute not in writes and expression.get("kind") in {
                "number",
                "boolean",
                "decimal",
            }:
                record["constants"][attribute] = expression
            statements.append(
                {
                    "kind": "assign",
                    "name": f"{record['prefix']}_{attribute}",
                    "inferred": True,
                    "value": expression,
                    "static_field": True,
                    "egx_numeric_property": record.get("egx_shape") and attribute in {"rotation_x", "rotation_y", "rotation_z", "scale"},
                    **location(record["declaration"]),
                }
            )
        initializers[id(record["declaration"])] = statements

    body = []
    for statement in tree["body"]:
        if id(statement) in initializers:
            body.extend(initializers[id(statement)])
        elif statement.get("kind") == "class":
            body.append(statement)
        else:
            body.append(rewrite(statement))

    # Defaults that no call overrides are constants in the specialized method,
    # not writable globals or extra stack arguments.
    for name, function in specialized.items():
        calls = [
            node
            for node, _, _ in walk(body + list(specialized.values()))
            if node.get("kind") == "call" and node.get("name") == name
        ]
        constants = {}
        for index, parameter in enumerate(function["params"]):
            default = parameter.get("default")
            if default is None or fold(default).get("kind") not in {
                "number",
                "boolean",
            }:
                continue
            if any(
                node.get("kind") in {"assign", "declare", "for"}
                and node.get("name") == parameter["name"]
                for node, _, _ in walk(function["body"])
            ):
                continue
            supplied = any(
                sum(argument.get("kind") != "keyword" for argument in call["arguments"])
                > index
                or any(
                    argument.get("kind") == "keyword"
                    and argument["name"] == parameter["name"]
                    for argument in call["arguments"]
                )
                for call in calls
            )
            if not supplied:
                constants[parameter["name"]] = fold(default)
        if constants:
            function["params"] = [
                parameter
                for parameter in function["params"]
                if parameter["name"] not in constants
            ]
            function["body"] = prune(rewrite(function["body"], substitutions=constants))

    all_functions = {
        node["name"]: node
        for node in body + list(specialized.values())
        if node.get("kind") == "function"
    }

    def transitive(name, target=None, seen=frozenset()):
        if name in seen:
            return set()
        function = all_functions.get(name)
        if function is None:
            return set()
        found = {
            node["name"]
            for node, _, _ in walk(function["body"])
            if node.get("kind") == "assign"
        }
        for node, _, _ in walk(function["body"]):
            if node.get("kind") == "call" and node["name"] in all_functions:
                if target and node["name"] == target:
                    found.add("$recursive")
                found.update(transitive(node["name"], target, seen | {name}))
        return found

    recursive = {name for name in specialized if "$recursive" in transitive(name, name)}
    serial = 0

    def inline(items):
        nonlocal serial
        result = []
        for statement in items:
            value = statement.get("value", {})
            function = (
                specialized.get(value.get("name"))
                if value.get("kind") == "call" and value.get("name") not in recursive
                else None
            )
            returns = (
                [node for node, _, _ in walk(function["body"])] if function else []
            )
            final_return = bool(
                function
                and function["body"]
                and function["body"][-1].get("kind") == "return"
                and sum(node.get("kind") == "return" for node in returns) == 1
            )
            void_inline = (
                statement.get("kind") == "expression_statement"
                and function
                and not any(node.get("kind") == "return" for node in returns)
            )
            value_inline = (
                statement.get("kind") in {"declare", "assign"} and final_return
            )
            if void_inline or value_inline:
                serial += 1
                bindings, order = bind(value, function["params"])
                locals_ = {
                    name
                    for node, _, _ in walk(function["body"])
                    if node.get("kind") in {"declare", "for"}
                    or node.get("kind") == "assign"
                    and node.get("inferred")
                    for name in node.get("names", [node["name"]])
                }
                written = transitive(function["name"])
                used = {
                    node["name"]
                    for node, _, _ in walk(function["body"])
                    if node.get("kind") == "name"
                }
                for parameter_name in order:
                    argument = bindings[parameter_name]
                    if parameter_name not in written and (
                        argument.get("kind") in {"number", "boolean"}
                        or argument.get("kind") == "name"
                        and argument["name"] not in written
                        and parameter_name in used
                    ):
                        parameter = next(
                            p for p in function["params"] if p["name"] == parameter_name
                        )
                        literal = fold(argument)
                        if literal.get("kind") in {"number", "boolean"}:
                            expected = parameter["type"]
                            if (
                                (expected == "bool") != (literal["kind"] == "boolean")
                                or literal["kind"] == "number"
                                and not -32768 <= literal["value"] <= 32767
                            ):
                                raise CompileError(
                                    "Invalid scalar method argument type or range",
                                    argument,
                                )
                            bindings[parameter_name] = literal
                            continue
                        bindings[parameter_name] = {
                            "kind": "argument_check",
                            "expected_type": parameter["type"],
                            "value": argument,
                            **location(argument),
                        }
                        continue
                    temporary = f"$inline_{serial}_{parameter_name}"
                    parameter = next(
                        p for p in function["params"] if p["name"] == parameter_name
                    )
                    result.append(
                        {
                            "kind": "declare",
                            "name": temporary,
                            "type": parameter["type"],
                            "value": argument,
                            **location(statement),
                        }
                    )
                    bindings[parameter_name] = {
                        "kind": "name",
                        "name": temporary,
                        **location(statement),
                    }
                renamed = {name: f"$inline_{serial}_{name}" for name in locals_}
                renamed.update(
                    {
                        name: value["name"]
                        for name, value in bindings.items()
                        if name in written and value.get("kind") == "name"
                    }
                )
                expanded = prune(rewrite(function["body"], substitutions=bindings))
                for node, _, _ in walk(expanded):
                    if (
                        node.get("kind") in {"name", "declare", "assign", "for"}
                        and node.get("name") in renamed
                    ):
                        node["name"] = renamed[node["name"]]
                    if "names" in node:
                        node["names"] = [
                            renamed.get(name, name) for name in node["names"]
                        ]
                if value_inline:
                    assignment = deepcopy(statement)
                    assignment["value"] = expanded[-1]["value"]
                    expanded = expanded[:-1] + [assignment]
                result.extend(inline(expanded))
                continue
            for key in ("body", "alternative", "final"):
                if (
                    isinstance(statement.get(key), list)
                    and statement.get("kind") != "class"
                ):
                    statement[key] = inline(statement[key])
            result.append(statement)
        return result

    tree["body"] = inline(list(specialized.values()) + body)
    tree["static_records"] = {
        name: {
            attribute: f"{record['prefix']}_{attribute}"
            for attribute in record["fields"]
        }
        for name, record in records.items()
    }
