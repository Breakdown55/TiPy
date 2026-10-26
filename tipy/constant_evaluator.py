"""Bounded interpreter of checked TiPy IR. Never executes source as Python.

Unknown operations, exhausted budgets and runtime errors decline specialization.
Framebuffer bits remain symbolic until explicitly cleared/set, so the optimizer
does not assume calculator RAM starts at zero.
"""


class NotStatic(Exception):
    pass


class Returned(Exception):
    def __init__(self, value): self.value = value


class Broken(Exception): pass
class Continued(Exception): pass


def i16(value):
    return ((int(value) + 32768) & 65535) - 32768


class Evaluator:
    def __init__(self, tree, *, budget=100000):
        self.functions = {item["label"]: item for item in tree["body"] if item["kind"] == "function"}
        self.globals, self.frame = {}, None
        self.function = None
        self.budget, self.steps, self.depth, self.calls, self.iterations = budget, 0, 0, 0, 0
        self.keep, self.bits = bytearray([255] * 768), bytearray(768)
        self.frames, self.collectors = [], []
        self.mutated_ids = set()

    def tick(self):
        self.steps += 1
        if self.steps > self.budget:
            raise NotStatic("evaluation budget exceeded")

    def cast(self, value, type_name):
        if type_name.endswith("[]"):
            return value
        return bool(value) if type_name == "bool" else int(value) & 255 if type_name == "u8" else i16(value)

    def read(self, item):
        env = self.frame if item.get("storage") == "frame" else self.globals
        try:
            return env[item["symbol"]]
        except (KeyError, TypeError):
            raise NotStatic("value is not known") from None

    def store(self, item, value):
        env = self.frame if item.get("storage") == "frame" else self.globals
        env[item["symbol"]] = self.cast(value, item["type"])

    def bounded(self, values):
        if len(values) > 16384:
            raise NotStatic("constant container is too large")
        return values

    def expr(self, item):
        self.tick()
        kind = item["kind"]
        if item.get("type") in {"fixed", "str"} or kind in {"stdlib", "cast_int", "to_fixed", "decimal", "compare_chain", "text_print", "text_input"}:
            raise NotStatic("native numeric or runtime effect")
        if kind == "void_constant": return None
        if kind in {"number", "boolean"}:
            return self.cast(item["value"], item["type"])
        if kind == "constant_list":
            return list(item["values"])
        if kind == "name": return self.read(item)
        if kind == "list":
            return self.bounded([self.cast(self.expr(value), item["type"][:-2]) for value in item["values"]])
        if kind == "comprehension":
            result = []
            self.collectors.append(result)
            try: self.block([item["loop"]])
            finally: self.collectors.pop()
            return result
        if kind == "length": return len(self.expr(item["arguments"][0]))
        if kind == "index": return self.expr(item["base"])[self.expr(item["index"])]
        if kind == "method":
            base = self.expr(item["base"])
            args = [self.expr(arg) for arg in item["arguments"]]
            name = item["name"]
            if name in {"append", "insert", "remove", "count", "index"}:
                value_index = 1 if name == "insert" else 0
                args[value_index] = self.cast(args[value_index], item["base"]["type"][:-2])
            methods = {"append": base.append, "insert": base.insert, "pop": base.pop,
                       "remove": base.remove, "clear": base.clear, "extend": base.extend,
                       "reverse": base.reverse, "copy": base.copy, "count": base.count, "index": base.index}
            if name not in methods: raise NotStatic("unknown list operation")
            if name in {"append", "insert", "pop", "remove", "clear", "extend", "reverse"}:
                self.mutated_ids.add(id(base))
            value = methods[name](*args)
            self.bounded(base)
            return value
        if kind == "builtin":
            args = [self.expr(arg) for arg in item["arguments"]]
            name = item["name"]
            if name == "range":
                values = range(*args)
                if len(values) > 16384: raise NotStatic("constant range is too large")
                return list(values)
            if name == "list": return list(args[0]) if args else []
            if name == "abs": return i16(abs(args[0]))
            if name == "sum": return i16(sum(args[0], args[1] if len(args) > 1 else 0))
            if name in {"min", "max"}:
                return (min if name == "min" else max)(*args)
            if name == "any": return any(args[0])
            if name == "all": return all(args[0])
            raise NotStatic("unknown builtin")
        if kind == "graphics":
            self.graphics(item["name"], [self.expr(arg) for arg in item["arguments"]])
            return None
        if kind == "call":
            args = [self.expr(arg) for arg in item["arguments"]]
            function = self.functions.get(item["symbol"])
            if function is None or self.depth >= 64: raise NotStatic("unknown or deeply recursive call")
            previous, previous_function = self.frame, self.function
            self.frame, self.function = {}, function
            self.depth += 1; self.calls += 1
            for param, value in zip(function["params"], args):
                slot = function["locals"][param["name"]]
                self.frame[slot["label"]] = self.cast(value, slot["type"])
            try:
                self.block(function["body"])
                return None
            except Returned as returned:
                return returned.value
            finally:
                self.frame, self.function = previous, previous_function
                self.depth -= 1
        if kind == "negate": return i16(-self.expr(item["value"]))
        if kind == "not": return not self.expr(item["value"])
        if kind == "and": return bool(self.expr(item["left"]) and self.expr(item["right"]))
        if kind == "or": return bool(self.expr(item["left"]) or self.expr(item["right"]))
        if kind == "membership":
            value = self.expr(item["left"])
            result = value in self.expr(item["right"])
            return not result if item["negate"] else result
        left, right = self.expr(item["left"]), self.expr(item["right"])
        if kind == "compare":
            return {"==": lambda: left == right, "!=": lambda: left != right,
                    "<": lambda: left < right, ">": lambda: left > right,
                    "<=": lambda: left <= right, ">=": lambda: left >= right}[item["operator"]]()
        operations = {"add": lambda: left + right, "subtract": lambda: left - right,
                      "multiply": lambda: left * right, "floor_divide": lambda: left // right,
                      "modulo": lambda: left % right}
        if kind not in operations: raise NotStatic("unknown expression")
        return i16(operations[kind]())

    def block(self, body):
        for item in body:
            self.tick()
            kind = item["kind"]
            if item.get("unreachable") or kind in {"function", "pass"}: continue
            if kind in {"declare", "assign"}: self.store(item, self.expr(item["value"]))
            elif kind == "expression_statement": self.expr(item["value"])
            elif kind == "collect":
                self.collectors[-1].append(self.cast(self.expr(item["value"]), item["type"]))
                self.bounded(self.collectors[-1])
            elif kind == "index_assign":
                base = self.expr(item["target"]["base"]); index = self.expr(item["target"]["index"])
                op = item["operator"]
                old = base[index] if op != "=" else None
                value = self.expr(item["value"])
                if op != "=":
                    value = {"+=": lambda: old + value, "-=": lambda: old - value,
                             "*=": lambda: old * value, "//=": lambda: old // value,
                             "%=": lambda: old % value}[op]()
                base[index] = self.cast(value, item["target"]["type"])
                self.mutated_ids.add(id(base))
            elif kind == "return": raise Returned(self.expr(item["value"]) if item["value"] is not None else None)
            elif kind == "break": raise Broken()
            elif kind == "continue": raise Continued()
            elif kind == "if": self.block(item["body"] if self.expr(item["condition"]) else item["alternative"])
            elif kind == "while":
                while self.expr(item["condition"]):
                    self.tick(); self.iterations += 1
                    try: self.block(item["body"])
                    except Continued: continue
                    except Broken: break
            elif kind == "for":
                if item["iteration_kind"] == "tuple":
                    rows = [[self.expr(value) for value in row["values"]] for row in item["iterable"]["values"]]
                    for row in rows:
                        self.tick(); self.iterations += 1
                        for target, value in zip(item["targets"], row): self.store(target, value)
                        try: self.block(item["body"])
                        except Continued: continue
                        except Broken: break
                elif item["iteration_kind"] == "range":
                    values = range(*[self.expr(arg) for arg in item["iterable"]["arguments"]])
                    for value in values:
                        self.tick(); self.iterations += 1; self.store(item["target"], value)
                        try: self.block(item["body"])
                        except Continued: continue
                        except Broken: break
                else:
                    sources = [self.expr(source) for source in item["sources"]]
                    counter = self.expr(item["enumeration_start"]) if item["iteration_kind"] == "enumerate" else 0
                    index = 0
                    while all(index < len(source) for source in sources):
                        self.tick(); self.iterations += 1
                        values = [source[index] for source in sources]
                        if item["iteration_kind"] == "enumerate": values.insert(0, i16(counter))
                        for target, value in zip(item["targets"], values): self.store(target, value)
                        try: self.block(item["body"])
                        except Continued: pass
                        except Broken: break
                        index += 1; counter += 1
            else: raise NotStatic("unknown statement")

    def pixel(self, x, y, operation="pixel_on"):
        self.tick()
        if not (0 <= x < 96 and 0 <= y < 64): return
        index, mask = y * 12 + x // 8, 128 >> (x % 8)
        if operation == "flip_pixel": self.bits[index] ^= mask
        else:
            self.keep[index] &= 255 ^ mask
            if operation == "pixel_on": self.bits[index] |= mask
            else: self.bits[index] &= 255 ^ mask

    def graphics(self, name, args):
        if name == "clear":
            self.keep[:] = bytes(768); self.bits[:] = bytes(768)
        elif name == "render":
            if len(self.frames) >= 64: raise NotStatic("too many observable frames")
            self.frames.append((bytes(self.keep), bytes(self.bits)))
        elif name in {"pixel_on", "pixel_off", "flip_pixel"}: self.pixel(*args, operation=name)
        elif name in {"h_line", "v_line"}:
            x1, y1, x2, y2 = args
            if name == "h_line" and y1 == y2:
                for x in range(max(0, min(x1, x2)), min(95, max(x1, x2)) + 1): self.pixel(x, y1)
            elif name == "v_line" and x1 == x2:
                for y in range(max(0, min(y1, y2)), min(63, max(y1, y2)) + 1): self.pixel(x1, y)
        elif name == "line":
            a, b, c, d = args
            if max(a, c) < 0 or min(a, c) >= 96 or max(b, d) < 0 or min(b, d) >= 64: return
            swapped = abs(d - b) > abs(c - a)
            if swapped: a, b, c, d = b, a, d, c
            dx, dy, sx, sy = abs(c - a), abs(d - b), 1 if a < c else -1, 1 if b < d else -1
            for step in range(dx + 1):
                major, minor = a + sx * step, b + sy * ((dx // 2 + step * dy) // dx if dx else 0)
                self.pixel(minor, major) if swapped else self.pixel(major, minor)
        elif name == "rectangle":
            x1, x2, y1, y2, width, filled = args
            left, right, top, bottom = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
            for y in range(max(0, top), min(63, bottom) + 1):
                for x in range(max(0, left), min(95, right) + 1):
                    self.tick()
                    if filled or width > 0 and min(x-left, right-x, y-top, bottom-y) < width: self.pixel(x, y)
        else: raise NotStatic("unknown graphics operation")


STATIC_ERRORS = (NotStatic, ZeroDivisionError, IndexError, ValueError, TypeError, KeyError, OverflowError, RecursionError)
