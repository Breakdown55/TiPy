"""Observable-effect optimization pipeline with a reference escape hatch."""

from copy import deepcopy
from .constant_evaluator import Evaluator, STATIC_ERRORS, NotStatic
from .framebuffer_backend import lower_frames
from .types import managed


def walk(item):
    if isinstance(item, dict):
        if isinstance(item.get("kind"),str) or isinstance(item.get("label"),str) or isinstance(item.get("symbol"),str):yield item
        for value in item.values(): yield from walk(value)
    elif isinstance(item, list):
        for value in item: yield from walk(value)


def reachable(tree):
    functions = {item["label"]: item for item in tree["body"] if item["kind"] == "function"}
    def calls(items):
        return {node["symbol"] for node in walk(items) if node.get("kind") in {"call","closure_call","closure","function_ref","iterator_map","bound_method"} and "symbol" in node and not node.get("unreachable")}
    live = calls([item for item in tree["body"] if item["kind"] != "function"])
    pending = list(live)
    while pending:
        label = pending.pop()
        if label in functions:
            more = calls(functions[label]["body"]) - live
            live.update(more); pending.extend(more)
    old = len(functions)
    tree["body"] = [item for item in tree["body"] if item["kind"] != "function" or item["label"] in live]
    return old - sum(item["kind"] == "function" for item in tree["body"])


class ConstantPropagation:
    def __init__(self, tree, symbols):
        self.tree, self.symbols = tree, symbols
        self.folded, self.lists, self.attempts, self.work = 0, 0, 0, 0
        self.bindings = {(node.get("storage", "global"), node["symbol"]): node for node in walk(tree)
                         if node.get("kind") == "name" and "symbol" in node}
        self.public_globals = {symbol["label"] for symbol in symbols.values()}

    def roots(self, evaluator):
        globals_ = {key: value for key, value in evaluator.globals.items() if key in self.public_globals}
        local_labels = {slot["label"] for name, slot in evaluator.function["locals"].items() if not name.startswith("$")} if evaluator.function else set()
        frame = {key: value for key, value in (evaluator.frame or {}).items() if key in local_labels}
        return globals_, frame

    def invalidate(self, evaluator):
        evaluator.globals.clear()
        if evaluator.frame is not None: evaluator.frame.clear()
        evaluator.keep[:] = bytes([255]) * 768
        evaluator.bits[:] = bytes(768)
        evaluator.frames.clear()

    def try_fold(self, item, evaluator):
        if item["kind"] in {"number", "boolean", "constant_list", "void_constant"} or self.attempts >= 512 or self.work >= 100000: return False
        self.attempts += 1
        trial = Evaluator(self.tree, budget=5000)
        trial.globals, trial.frame = deepcopy((evaluator.globals, evaluator.frame))
        trial.function = evaluator.function
        trial.keep[:], trial.bits[:] = evaluator.keep, evaluator.bits
        before = deepcopy(self.roots(trial))
        root_ids = {id(value) for root in self.roots(trial) for value in root.values() if isinstance(value, list)}
        try:
            value = trial.expr(item)
            if self.roots(trial) != before or root_ids & trial.mutated_ids or trial.frames or trial.keep != evaluator.keep or trial.bits != evaluator.bits:
                return False
        except STATIC_ERRORS:
            return False
        finally:
            self.work += trial.steps
        type_name = item.get("type", "void")
        if item["kind"] == "name" and isinstance(value, list): return False
        replacement = None
        if type_name == "void" and value is None:
            replacement = {"kind": "void_constant", "type": "void"}
        elif isinstance(value, list):
            for storage, env in [("global", trial.globals), ("frame", trial.frame or {})]:
                for symbol, existing in env.items():
                    binding = self.bindings.get((storage, symbol))
                    if storage == "frame" and trial.function:
                        binding = next(({**slot, "kind": "name", "name": name, "symbol": symbol}
                                        for name, slot in trial.function["locals"].items() if slot["label"] == symbol and not name.startswith("$")), None)
                    if storage == "global":
                        binding = next(({**slot, "kind": "name", "name": name, "symbol": symbol}
                                        for name, slot in self.symbols.items() if slot["label"] == symbol), None)
                    if existing is value and binding and (storage == "frame" or symbol in self.public_globals):
                        replacement = {**binding}
                        break
                if replacement: break
            if replacement is None:
                if len(value) > 2048: return False
                replacement = {"kind": "constant_list", "values": list(value), "type": type_name, "owned": True}
                self.lists += 1
        elif isinstance(value, (int, bool)):
            replacement = {"kind": "boolean" if type_name == "bool" else "number", "value": value, "type": type_name}
        if replacement is None: return False
        location = {key: item[key] for key in ("line", "column", "filename") if key in item}
        item.clear(); item.update(replacement); item.update(location)
        self.folded += 1
        return True

    def expression(self, item, evaluator):
        if item["kind"] in {"call", "graphics", "builtin"}:
            args = item["arguments"]
            if args and not all(arg["kind"] in {"number", "boolean"} for arg in args) and all(not arg["type"].endswith("[]") for arg in args):
                probe = {"kind": "list", "type": "i16[]", "values": args}
                if self.try_fold(probe, evaluator) and probe["kind"] == "constant_list":
                    self.lists -= 1
                    item["arguments"] = [{"kind": "boolean" if arg["type"] == "bool" else "number", "type": arg["type"],
                                           "value": bool(value) if arg["type"] == "bool" else value,
                                           **{key: arg[key] for key in ("line", "column", "filename") if key in arg}}
                                          for arg, value in zip(args, probe["values"])]
        if self.try_fold(item, evaluator): return
        # An earlier argument may mutate a later argument's inputs. When the
        # whole expression cannot be proven pure, only fold context-free parts.
        empty = Evaluator(self.tree, budget=5000)
        def literals(node):
            if not isinstance(node, dict): return
            if node.get("kind") == "comprehension": return
            for key in ("left", "right", "value", "base", "index"):
                if isinstance(node.get(key), dict): literals(node[key])
            for arg in node.get("arguments", []): literals(arg)
            if "kind" in node: self.try_fold(node, empty)
        literals(item)

    def simulate(self, statement, evaluator):
        start = evaluator.steps
        try:
            if self.work >= 100000: raise NotStatic("propagation budget exceeded")
            evaluator.block([statement])
            self.work += evaluator.steps - start
        except STATIC_ERRORS:
            self.work += max(0, evaluator.steps - start)
            self.invalidate(evaluator)
            evaluator.steps = 0

    def body(self, body, evaluator):
        output = []
        for item in body:
            if item.get("unreachable"): continue
            kind = item["kind"]
            if kind == "function":
                output.append(item); continue
            if kind == "if":
                self.expression(item["condition"], evaluator)
                if item["condition"]["kind"] == "boolean":
                    output.extend(self.body(item["body"] if item["condition"]["value"] else item["alternative"], evaluator))
                    self.folded += 1
                    continue
                self.invalidate(evaluator)
            elif kind in {"for", "while"}:
                if kind == "for" and item["iteration_kind"] == "range":
                    args = item["iterable"]["arguments"]
                    if not all(arg["kind"] == "number" for arg in args):
                        trial_args = {"kind": "list", "type": "i16[]", "values": args}
                        if self.try_fold(trial_args, evaluator) and trial_args["kind"] == "constant_list":
                            self.lists -= 1
                            item["iterable"]["arguments"] = [
                                {"kind": "number", "type": "i16", "value": value, "line": arg["line"], "column": arg["column"]}
                                for value, arg in zip(trial_args["values"], args)]
                # Loop-carried facts must be solved before propagation through
                # a loop body. Keep this region conservative on the fallback path.
                self.invalidate(evaluator)
            elif kind in {"declare", "assign", "expression_statement", "return"} and item.get("value") is not None:
                self.expression(item["value"], evaluator)
                if kind != "return": self.simulate(item, evaluator)
            else:
                self.invalidate(evaluator)
            output.append(item)
        return output

    def run(self):
        for function in [item for item in self.tree["body"] if item["kind"] == "function"]:
            evaluator = Evaluator(self.tree, budget=5000)
            evaluator.frame, evaluator.function = {}, function
            function["body"] = self.body(function["body"], evaluator)
        evaluator = Evaluator(self.tree, budget=5000)
        self.tree["body"] = self.body(self.tree["body"], evaluator)


def pure_total(item):
    if item.get("cell") or item.get("evaluations"):return False
    kind = item["kind"]
    if kind in {"number", "boolean", "name", "constant_list", "void_constant"}: return True
    if kind in {"negate", "not"}: return pure_total(item["value"])
    if kind in {"add", "subtract", "multiply", "compare", "and", "or"}:
        return pure_total(item["left"]) and pure_total(item["right"])
    if kind == "length": return pure_total(item["arguments"][0])
    if kind == "membership": return pure_total(item["left"]) and pure_total(item["right"])
    if kind == "builtin":
        if not all(pure_total(arg) for arg in item["arguments"]): return False
        if item["name"] in {"list", "abs", "sum", "any", "all"}: return True
        if item["name"] == "range":
            args = item["arguments"]
            return len(args) < 3 or args[2]["kind"] == "number" and args[2]["value"] != 0 or args[2]["kind"] == "negate" and args[2]["value"]["kind"] == "number" and args[2]["value"]["value"] != 0
        if item["name"] in {"min", "max"} and item.get("scalar"): return True
    return False


def inline_small_functions(tree):
    def cheap_argument(item):
        if managed(item.get("type", "")) or item.get("evaluations"): return False
        if item["kind"] in {"number", "boolean", "name"}: return True
        if item["kind"] in {"negate", "not"}: return cheap_argument(item["value"])
        if item["kind"] in {"add", "subtract", "multiply", "compare", "and", "or"}:
            return cheap_argument(item["left"]) and cheap_argument(item["right"])
        return False
    candidates = {}
    for function in [item for item in tree["body"] if item["kind"] == "function"]:
        body = [item for item in function["body"] if item["kind"] != "pass"]
        if len(body) != 1 or body[0]["kind"] != "return" or body[0]["value"] is None: continue
        if function.get("cell_names") or function.get("captures"):continue
        value = body[0]["value"]
        if managed(value["type"]) or not pure_total(value): continue
        if sum("kind" in node for node in walk(value)) > 8: continue
        params = [function["locals"][p["name"]]["label"] for p in function["params"]]
        if any(node.get("storage") == "frame" and node.get("symbol") not in params for node in walk(value) if node.get("kind") == "name"): continue
        candidates[function["label"]] = params, value
    count = 0
    for node in list(walk(tree["body"])):
        if node.get("kind") != "call" or node.get("symbol") not in candidates: continue
        args = node["arguments"]
        if any(not cheap_argument(arg) or sum("kind" in n for n in walk(arg)) > 4 for arg in args): continue
        params, value = candidates[node["symbol"]]
        replacements = dict(zip(params, args))
        replacement = deepcopy(value)
        for child in list(walk(replacement)):
            if child.get("kind") == "name" and child.get("storage") == "frame" and child["symbol"] in replacements:
                argument = deepcopy(replacements[child["symbol"]])
                child.clear(); child.update(argument)
        location = {key: node[key] for key in ("line", "column", "filename") if key in node}
        # Keyword/default binding evaluates source arguments into caller slots.
        # Keep those snapshots when replacing the call with its return value.
        if node.get("evaluations"):replacement["evaluations"]=deepcopy(node["evaluations"])
        node.clear(); node.update(replacement); node.update(location)
        count += 1
    return count


def eliminate_dead(tree, *, preserve_globals=False):
    removed = 0
    for _ in range(8):
        reachable(tree)
        reads = {node["symbol"] for node in walk(tree["body"]) if node.get("kind") == "name" and "symbol" in node and node.get("storage") != "frame"}
        changed = 0
        def body(items, local_reads=frozenset()):
            nonlocal changed
            def used(slot):
                return slot["symbol"] in local_reads if slot.get("storage") == "frame" else preserve_globals or slot["symbol"] in reads
            result = []
            for item in items:
                if item.get("unreachable"):
                    changed += 1; continue
                if item["kind"] == "pass":
                    changed += 1; continue
                if item["kind"] == "expression_statement" and item["value"]["kind"] == "void_constant":
                    changed += 1; continue
                if item["kind"] in {"declare", "assign"} and not used(item) and pure_total(item["value"]):
                    changed += 1; continue
                if item["kind"] in {"function", "if", "for", "while"}:
                    inner_reads = {node["symbol"] for node in walk(item["body"]) if node.get("kind") == "name" and node.get("storage") == "frame"} if item["kind"] == "function" else local_reads
                    item["body"] = body(item["body"], inner_reads)
                    if item["kind"] == "if": item["alternative"] = body(item["alternative"], local_reads)
                if item["kind"] == "for" and not item["body"]:
                    targets = [item["target"]] if item["iteration_kind"] == "range" else item["targets"]
                    if all(not used(target) for target in targets):
                        finite = pure_total(item["iterable"]) if item["iteration_kind"] == "range" else all(pure_total(source) for source in item["sources"])
                        if item["iteration_kind"] == "enumerate": finite = finite and pure_total(item["enumeration_start"])
                        if finite:
                            changed += 1; continue
                if item["kind"] == "if" and not item["body"] and not item["alternative"] and pure_total(item["condition"]):
                    changed += 1; continue
                result.append(item)
            return result
        tree["body"] = body(tree["body"])
        removed += changed
        if not changed: break
    return removed


def eliminate_unobserved_drawing(tree):
    writes = {"clear", "pixel_on", "pixel_off", "flip_pixel", "h_line", "v_line", "line", "rectangle"}
    known = {"program", "function", "parameter", "declare", "assign", "expression_statement", "for", "while", "if", "return", "pass", "break", "continue", "collect", "name", "number", "boolean", "list", "comprehension", "index", "index_assign", "method", "builtin", "length", "graphics", "call", "negate", "add", "subtract", "multiply", "floor_divide", "modulo", "compare", "and", "or", "not", "membership", "constant_list", "void_constant"}
    functions = {item["label"] for item in tree["body"] if item["kind"] == "function"}
    for node in walk(tree["body"]):
        if node.get("kind") and node["kind"] not in known: return 0
        if node.get("kind") == "graphics" and node["name"] not in writes: return 0
        if node.get("kind") == "builtin" and node["name"] not in {"range", "list", "abs", "sum", "min", "max", "any", "all"}: return 0
        if node.get("kind") == "call" and node.get("symbol") not in functions and node.get("name") not in {"enumerate", "zip"}: return 0
        if node.get("kind") == "method" and (not node["base"].get("type", "").endswith("[]") or node["name"] not in {"append", "insert", "pop", "remove", "clear", "extend", "reverse", "copy", "count", "index"}): return 0
    removed = 0
    for node in walk(tree["body"]):
        if node.get("kind") == "expression_statement" and node["value"]["kind"] == "graphics" and all(pure_total(arg) for arg in node["value"]["arguments"]):
            node.clear(); node.update(kind="pass", line=0)
            removed += 1
    return removed


def compact_frames(tree):
    removed = 0
    for function in [item for item in tree["body"] if item["kind"] == "function"]:
        used = {node.get("symbol", node.get("label")) for node in walk(function["body"]) if node.get("storage") == "frame"}
        old_size, size, offsets, slots = function["frame_size"], 0, {}, {}
        for name, slot in function["locals"].items():
            if slot["offset"] > 0:
                slots[name] = slot
            elif slot["label"] in used:
                size += slot["size"]
                offsets[slot["label"]] = -size
                slots[name] = {**slot, "offset": -size}
        for node in walk(function["body"]):
            label = node.get("symbol", node.get("label"))
            if node.get("storage") == "frame" and label in offsets:
                node["offset"] = offsets[label]
        function["locals"], function["frame_size"] = slots, size
        removed += old_size - size
    return removed


def specialize_ranges(tree):
    changed = 0
    for node in walk(tree["body"]):
        if node.get("kind") != "for" or node.get("iteration_kind") != "range" or "fast_range" in node: continue
        args = node["iterable"]["arguments"]
        if not all(arg["kind"] == "number" for arg in args): continue
        values = [arg["value"] for arg in args]
        if len(values) == 3 and values[2] == 0: continue
        sequence = range(*values)
        if not sequence:
            location = {key: node[key] for key in ("line", "column", "filename") if key in node}
            node.clear(); node.update(kind="pass", **location)
        else:
            node["fast_range"] = {"start": sequence[0], "last": sequence[-1], "step": sequence.step,
                                  "byte_compare": min(sequence[0], sequence[-1]) >= 0 and max(sequence[0], sequence[-1]) <= 255}
            node.pop("range_stop", None); node.pop("range_step", None)
        changed += 1
    return changed


def optimize_program(checked, symbols, mode):
    if mode not in {"reference", "balanced", "aggressive"}: raise ValueError("Unknown optimization mode")
    tree = deepcopy(checked)
    report = [{"pass": "mode", "value": mode}]
    if mode == "reference": return tree, symbols, report
    removed_functions = reachable(tree)
    report.append({"pass": "function-reachability", "removed": removed_functions})
    from .features import runtime_features
    tree["tracing_heap"],tree["generators"]=runtime_features(tree)
    if tree.get("tracing_heap") or any(n.get("cell") or n.get("kind") in {"closure","closure_call","function_ref"} for n in walk(tree)):
        report.append({"pass":"closure-alias-safety","preserved":True})
        reachable(tree)
        return tree,symbols,report
    if mode == "aggressive":
        report.append({"pass": "observable-effect-analysis", "unrendered_draws_removed": eliminate_unobserved_drawing(tree)})
        evaluator = Evaluator(tree)
        try:
            evaluator.block(tree["body"])
        except STATIC_ERRORS as error:
            report.append({"pass": "whole-program-specialization", "applied": False, "reason": str(error) or type(error).__name__})
        else:
            frames = lower_frames(evaluator.frames)
            report.extend([
                {"pass": "whole-program-specialization", "applied": True, "steps": evaluator.steps, "calls": evaluator.calls, "loop_iterations": evaluator.iterations},
                {"pass": "dead-state-elimination", "variables_removed": len(symbols), "heap_removed": True},
                {"pass": "framebuffer-lowering", "frames": len(frames), "encodings": [frame["encoding"] for frame in frames]},
            ])
            return {"kind": "program", "body": [], "static_frames": frames, "temporaries": {}}, {}, report
    for iteration in range(3):
        propagation = ConstantPropagation(tree, symbols)
        propagation.run()
        report.append({"pass": "constant-propagation", "iteration": iteration + 1, "folded": propagation.folded, "constant_lists": propagation.lists})
        report.append({"pass": "range-strength-reduction", "specialized": specialize_ranges(tree)})
        dead = eliminate_dead(tree, preserve_globals=mode == "balanced")
        inlined = inline_small_functions(tree)
        report.append({"pass": "dead-state-elimination", "removed": dead})
        report.append({"pass": "small-function-inlining", "inlined": inlined})
        if not (propagation.folded or dead or inlined): break
    report.append({'pass':'static-pixel-batches','loops_lowered':lower_pixel_loops(tree)})
    reachable(tree)
    report.append({"pass": "frame-compaction", "bytes_removed": compact_frames(tree)})
    mentioned = {node.get("symbol") for node in walk(tree["body"]) if "symbol" in node}
    tree["temporaries"] = {name: slot for name, slot in tree.get("temporaries", {}).items() if slot["label"] in mentioned}
    emitted = symbols if mode == "balanced" else {name: symbol for name, symbol in symbols.items() if symbol["label"] in mentioned}
    return tree, emitted, report


def lower_pixel_loops(tree):
    """Collapse isolated constant pixel loops without assuming existing RAM."""
    lowered=0
    allowed={'for','if','pass','expression_statement','graphics','name','number','boolean',
             'builtin','add','subtract','multiply','floor_divide','modulo','negate','not',
             'compare','and','or','bit_and','bit_or','bit_xor','shift_left','shift_right'}
    def visit(item):
        nonlocal lowered
        if isinstance(item,list):
            for value in item:visit(value)
            return
        if not isinstance(item,dict):return
        if item.get('kind')=='for':
            nodes=list(walk(item))
            loops=[n for n in nodes if n.get('kind')=='for']
            targets={n['target']['symbol']:n['target'] for n in loops if n.get('iteration_kind')=='range'}
            safe=all(n.get('kind') in allowed or 'kind' not in n for n in nodes)
            safe=safe and all(n.get('iteration_kind')=='range' for n in loops)
            safe=safe and all(n.get('kind')!='name' or n['symbol'] in targets for n in nodes)
            safe=safe and all(n.get('kind')!='builtin' or n.get('name')=='range' for n in nodes)
            pixels=[n for n in nodes if n.get('kind')=='graphics']
            safe=safe and bool(pixels) and all(n['name'] in {'pixel_on','pixel_off','flip_pixel'} for n in pixels)
            if safe:
                trial=Evaluator(tree,budget=100000);trial.frame={}
                try:trial.block([item])
                except STATIC_ERRORS:pass
                else:
                    operations=[];index=0
                    while index<768:
                        keep,bits=trial.keep[index],trial.bits[index]
                        if keep==255 and bits==0:index+=1;continue
                        end=index+1
                        while end<768 and trial.keep[end]==keep and trial.bits[end]==bits:end+=1
                        operations.append((index,end-index,keep,bits));index=end
                    finals=[]
                    for target in targets.values():
                        env=trial.frame if target.get('storage')=='frame' else trial.globals
                        if target['symbol'] in env:finals.append({'target':deepcopy(target),'value':env[target['symbol']]})
                    location={k:item[k] for k in ('line','column','filename') if k in item}
                    item.clear();item.update(kind='pixel_batch',operations=operations,finals=finals,**location)
                    lowered+=1;return
        for value in item.values():visit(value)
    visit(tree['body'])
    return lowered
