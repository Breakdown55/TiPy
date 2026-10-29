"""Checked dictionaries -> assembly. Every expression leaves its result in HL."""

from .list_runtime import runtime_assembly, HEAP_BYTES, RUNTIME_TARGET
from .optimizer import optimize
from .types import (
    managed,
    is_dict,
    dict_types,
    release,
    is_tuple,
    tuple_types,
    width as array_width,
    all_types,
    is_set,
    element as element_type,
    is_view,
    view_types,
    is_iterator,
    iterator_types,
    iterable_element as iterable_element_type,
)

# Larger models keep the compact interpreter to stay within calculator RAM.
MAX_SPECIALIZED_MODEL_VERTICES = 32
MAX_SPECIALIZED_SCENE_VERTICES = 48


def _decode_mesh_vertices(packet):
    data = packet["data"]
    vertices = []
    for vertex_index in range(packet["vertex_count"]):
        coordinates = []
        for axis in range(3):
            offset = 10 + vertex_index * 6 + axis * 2
            value = data[offset] | (data[offset + 1] << 8)
            if value & 0x8000:
                value -= 0x10000
            coordinates.append(value)
        vertices.append(tuple(coordinates))
    return vertices


def generate(tree, symbols, *, optimize_graphics=True, _heap_bytes=HEAP_BYTES):
    original_tree = tree
    from .argument_optimizer import simplify as simplify_arguments

    tree = simplify_arguments(tree)

    def string_field(item, key):
        value = item.get(key)
        return value if isinstance(value, str) else None

    if "static_frames" in tree:
        from .framebuffer_backend import generate_frames

        return generate_frames(tree["static_frames"])

    def called_labels(item):
        if isinstance(item, dict):
            if item.get("unreachable") is True:
                return set()
            found = (
                {item["symbol"]}
                if string_field(item, "kind")
                in {
                    "call",
                    "closure_call",
                    "closure",
                    "function_ref",
                    "iterator_map",
                    "bound_method",
                }
                and "symbol" in item
                else set()
            )
            return found | set().union(
                *(called_labels(value) for value in item.values())
            )
        if isinstance(item, list):
            return set().union(*(called_labels(value) for value in item))
        return set()

    functions = {
        item["label"]: item for item in tree["body"] if item["kind"] == "function"
    }
    live = called_labels([item for item in tree["body"] if item["kind"] != "function"])
    pending = list(live)
    while pending:
        name = pending.pop()
        if name in functions:
            dependencies = called_labels(functions[name]["body"]) - live
            live.update(dependencies)
            pending.extend(dependencies)
    tree = {
        **tree,
        "body": [
            item
            for item in tree["body"]
            if item["kind"] != "function" or item["label"] in live
        ],
    }
    # Compiler-owned graphics state is initialized only when a reachable helper
    # reads it. Unused drawing functions must not introduce a heap or matrices.
    state = {
        n["symbol"]: n
        for n in tree["body"]
        if n.get("kind") == "declare" and n.get("name", "").startswith("$__tipy_gfx_")
    }

    def reads(node):
        if isinstance(node, dict):
            return (
                {node["symbol"]}
                if node.get("kind") == "name" and "symbol" in node
                else set()
            ) | set().union(*(reads(v) for v in node.values()))
        if isinstance(node, list):
            return set().union(*(reads(v) for v in node))
        return set()

    needed = reads([n for n in tree["body"] if n.get("symbol") not in state])
    pending = list(needed & state.keys())
    while pending:
        dependencies = reads(state[pending.pop()]) - needed
        needed.update(dependencies)
        pending.extend(dependencies & state.keys())
    removed = set(state) - needed
    tree = {
        **tree,
        "body": [n for n in tree["body"] if n.get("symbol") not in removed],
        "temporaries": {
            k: v
            for k, v in tree.get("temporaries", {}).items()
            if v["label"] not in removed
        },
    }
    from .drawing_optimizer import optimize_drawing

    if optimize_graphics:
        tree = optimize_drawing(tree)

    def has_live_mesh(node):
        if isinstance(node, dict):
            if node.get("unreachable"):
                return False
            return (
                node.get("kind") == "native_graphics"
                and node.get("name") == "mesh_draw"
                or any(has_live_mesh(value) for value in node.values())
            )
        return isinstance(node, list) and any(has_live_mesh(value) for value in node)

    approximate_mesh = tree.get("render_quality") == "aggressive" and has_live_mesh(
        tree["body"]
    )

    def mesh_packets(node):
        if isinstance(node, dict):
            if (
                node.get("kind") == "geometry_data"
                and not node.get("compact")
                and not node.get("collision_map")
            ):
                yield node
            for value in node.values():
                yield from mesh_packets(value)
        elif isinstance(node, list):
            for value in node:
                yield from mesh_packets(value)

    mesh_geometry = list(mesh_packets(tree["body"]))
    compiled_mesh_vertices = (
        not tree.get("mesh_vertices_disabled")
        and has_live_mesh(tree["body"])
        and bool(mesh_geometry)
        and all(
            0 < n.get("vertex_count", 100) <= MAX_SPECIALIZED_MODEL_VERTICES
            and n.get("face_count", 0) > 0
            for n in mesh_geometry
        )
        and sum(n["vertex_count"] for n in mesh_geometry)
        <= MAX_SPECIALIZED_SCENE_VERTICES
    )
    mesh_programs = {}
    from .drawing_optimizer import annotate_blank_meshes, annotate_legacy_lighting

    annotate_blank_meshes(tree)
    annotate_legacy_lighting(tree)
    blank_mesh_spans = compiled_mesh_vertices and any(
        n.get("hull_bounds") for n in mesh_geometry
    )

    code = ["; Generated by TiPy", "        org 0x9D95"]
    serial = 0
    loops = []  # Innermost (condition label, exit label) is at the top.
    unwind = []
    return_label = None

    def has_arrays(item):
        if isinstance(item, dict):
            if item.get("kind") == "native_matrix" and item.get("name") in {
                "mat_push",
                "mat_pop",
            }:
                return True
            if item.get("kind") == "graphics" and item.get("name") == "triangle":
                return True
            if item.get("unreachable") is True:
                return False
            if item.get("iteration_kind") == "range":
                return has_arrays(item["body"]) or has_arrays(
                    item["iterable"]["arguments"]
                )
            if (
                string_field(item, "kind") in {"text_print", "text_input"}
                and item["arguments"][0]["kind"] == "string"
            ):
                return item.get("type") == "str" or has_arrays(item["arguments"][1:])
            return (
                managed(string_field(item, "type") or "")
                or string_field(item, "kind") in {"list", "index", "method", "length"}
                or any(has_arrays(v) for v in item.values())
            )
        if isinstance(item, list):
            return any(has_arrays(v) for v in item)
        return False

    from .features import runtime_features

    tracing, generators = runtime_features(tree)
    lists_used = has_arrays(tree) or tracing
    modulo_used = False
    math_used = set()
    pixels_used = set()
    constant_data = {}
    geometry_packets = {}
    vertex_programs = {}
    iterator_helpers = {}
    iterator_code = []
    iterator_lists = {}
    iterator_new_used = False
    argument_binders = {}
    truth_helpers = {}
    aggregate_helpers = {}

    def ensure_truth(t):
        if t not in truth_helpers:
            from .truth_runtime import routine

            label = f"__obj_truth_kind_{len(truth_helpers)}"
            truth_helpers[t] = label
            iterator_code.append(routine(t, label, ensure_truth))
        return truth_helpers[t]

    def ensure_iterator(t):
        if t not in iterator_helpers:
            name = f"__obj_iterator_{len(iterator_helpers)}"
            iterator_helpers[t] = name
            from .iterator_runtime import next_routine

            iterator_code.append(
                next_routine(
                    t,
                    name,
                    ensure_iterator,
                    {f["name"]: f for f in functions.values()},
                    ensure_truth,
                )
            )
        return iterator_helpers[t]

    from .optimization_pipeline import walk

    def may_call_user(item):
        return any(
            n.get("kind")
            in {
                "call",
                "closure_call",
                "iterator_next",
                "iterator_list",
                "iterator_aggregate",
            }
            or n.get("kind") == "for"
            and n.get("iteration_kind") == "iterator"
            for n in walk(item)
        )

    native_used = {
        item["name"] for item in walk(tree["body"]) if item.get("kind") == "stdlib"
    }
    # A small acyclic scalar program has a statically bounded source stack.
    # Keep 768 bytes for native kernels and 128 for expression temporaries;
    # source frames may use at most the remaining 128 of the reserved 1 KB.
    bounded_stack = False
    if not lists_used and not generators and not any(node.get("kind") in {"closure_call","closure","function_ref","bound_method"} for node in walk(tree["body"])):
        cache = {}
        def source_stack(name, pending):
            if name in pending:return 1025
            if name in cache:return cache[name]
            function=functions[name]
            callees=called_labels(function["body"]) & live & functions.keys()
            weight=function["frame_size"] + 2*len(function["params"]) + 4
            cache[name]=weight+max((source_stack(callee,pending|{name}) for callee in callees),default=0)
            return cache[name]
        def expression_depth(value):
            if isinstance(value,dict):return 1+max((expression_depth(child) for child in value.values()),default=0)
            if isinstance(value,list):return max((expression_depth(child) for child in value),default=0)
            return 0
        bounded_stack = max((source_stack(name,set()) for name in live & functions.keys()),default=0)<=128 and expression_depth(tree["body"])<=32
    if any(item.get("kind") == "native_graphics" and item.get("name") in {"mesh_game_begin", "mesh_session_begin", "mesh_session_end"} for item in walk(tree["body"])):
        native_used.update({"time.frame_ticks", "time.stop_frame_timer"})
    text_nodes = [
        item
        for item in walk(tree["body"])
        if item.get("kind") in {"text_print", "text_input"}
    ]
    if any(
        n["arguments"][0]["type"] in {"i16", "u8", "bool"}
        and any(may_call_user(v) for v in n["arguments"][1:])
        for n in text_nodes
    ):
        lists_used = True
    text_used = {"draw"} if text_nodes else set()
    conversions = {n["name"] for n in walk(tree) if n.get("kind") == "string_builtin"}
    if {"number", "fixed"} & conversions:
        text_used.add("number")
    if "boolean" in conversions:
        text_used.add("bool")
    if any(item["kind"] == "text_input" for item in text_nodes):
        text_used.add("input")
        native_used.add("time.sleep")
        text_used.update(
            "input_int" if item["type"] == "i16" else "input_str"
            for item in text_nodes
            if item["kind"] == "text_input"
        )
    if any(
        item["kind"] == "text_print" and item["arguments"][0]["type"] in {"i16", "u8"}
        for item in text_nodes
    ):
        text_used.add("number")
    if any(
        item["kind"] == "text_print" and item["arguments"][0]["type"] == "bool"
        for item in text_nodes
    ):
        text_used.add("bool")
    if "input" in text_used:
        pixels_used.update({"flip_pixel", "render"})
    if native_used & {"math.sin", "math.cos", "math.radians"}:
        modulo_used = True
    if native_used & {"math.radians", "random.randint"}:
        math_used.add("mul")
    if "math.radians" in native_used:
        math_used.add("div")

    def has_modulo(item):
        if isinstance(item, dict):
            if item.get("unreachable") is True:
                return False
            return (
                item.get("iteration_kind") == "range"
                and "fast_range" not in item
                or string_field(item, "kind") in {"modulo", "floor_divide"}
                or string_field(item, "operator") in {"//=", "%="}
                or any(has_modulo(value) for value in item.values())
            )
        return isinstance(item, list) and any(has_modulo(value) for value in item)

    errors_used = (
        lists_used
        or has_modulo(tree)
        or bool(native_used)
        or any(
            n.get("kind")
            in {
                "function",
                "power",
                "shift_left",
                "shift_right",
                "numeric_builtin",
                "true_divide",
                "native_matrix",
                "native_graphics",
            }
            for n in walk(tree)
        )
    )

    def label():
        nonlocal serial
        serial += 1
        return f"__L{serial}"

    def mark(name):
        code.append(name + ":")

    def emit(*instructions):
        code.extend("        " + instruction for instruction in instructions)

    def frame_address(offset):
        return f"(ix{offset:+d})"

    def buffer_address(slot):
        if slot.get("storage") == "frame":
            emit("push ix", "pop hl", f"ld de,{slot['offset'] & 65535}", "add hl,de")
        else:
            emit(f"ld hl,{slot['label']}")

    def load(item):
        if item.get("cell"):
            cell_pointer(item)
            emit("push hl", "call __obj_cell_get", "pop de")
            return
        byte = item["type"] in {"u8", "bool"}
        if item.get("storage") == "frame":
            if not -128 <= item["offset"] <= 126:
                emit("push ix", "pop hl", f"ld de,{item['offset']&65535}", "add hl,de")
                if byte:
                    emit("ld l,(hl)", "ld h,0")
                else:
                    emit("ld e,(hl)", "inc hl", "ld d,(hl)", "ex de,hl")
                return
            emit(f"ld l,{frame_address(item['offset'])}")
            emit("ld h,0" if byte else f"ld h,{frame_address(item['offset'] + 1)}")
        elif byte:
            emit(f"ld a,({item['symbol']})", "ld l,a", "ld h,0")
        else:
            emit(f"ld hl,({item['symbol']})")

    def store(item):
        if item.get("cell"):
            emit("push hl")
            cell_pointer(item)
            emit(
                "pop de",
                "push hl",
                "push de",
                "call __obj_cell_set",
                "pop de",
                "pop de",
            )
            return
        byte = item["type"] in {"u8", "bool"}
        if item.get("storage") == "frame":
            if not -128 <= item["offset"] <= 126:
                emit(
                    "push hl",
                    "push ix",
                    "pop hl",
                    f"ld de,{item['offset']&65535}",
                    "add hl,de",
                    "pop de",
                    "ld (hl),e",
                )
                if not byte:
                    emit("inc hl", "ld (hl),d")
                emit("ex de,hl")
                return
            emit(f"ld {frame_address(item['offset'])},l")
            if not byte:
                emit(f"ld {frame_address(item['offset'] + 1)},h")
        elif byte:
            emit("ld a,l", f"ld ({item['symbol']}),a")
        else:
            emit(f"ld ({item['symbol']}),hl")

    def cell_pointer(item):
        if "capture_index" in item:
            load({**item["env_slot"], "symbol": item["env_slot"]["label"]})
            emit(
                "push hl",
                f"ld hl,{item['capture_index']}",
                "push hl",
                "call __obj_read",
                "pop de",
                "pop de",
            )
        else:
            load({k: v for k, v in item.items() if k != "cell"})

    def store_borrowed(target):
        if managed(target["type"]):
            emit("push hl", "call __arr_retain", "pop de")
            if not target.get("cell"):
                emit("push hl")
                load(target)
                emit("push hl", f"call {release(target['type'])}", "pop de", "pop hl")
        store(target)

    def runtime(name, arguments, prefix="__arr_"):
        protected = []
        for index, argument in enumerate(arguments):
            hold = (
                isinstance(argument, dict)
                and argument.get("kind") not in {"stack_dict", "unpack_source"}
                and managed(argument.get("type", ""))
                and not argument.get("owned")
                and any(
                    may_call_user(later)
                    for later in arguments[index + 1 :]
                    if isinstance(later, dict)
                )
            )
            protected.append(hold)
            if isinstance(argument, int):
                emit(f"ld hl,{argument & 65535}")
            else:
                expression(argument)
            if hold:
                emit("push hl", "call __arr_retain", "pop de")
            emit("push hl")
        emit(f"call {prefix}{name}")
        for argument, hold in reversed(list(zip(arguments, protected))):
            emit("pop de")
            if isinstance(argument, dict) and (argument.get("owned") or hold):
                emit(
                    "push hl",
                    "push de",
                    f"call {release(argument['type'])}",
                    "pop de",
                    "pop hl",
                )

    def read_length():
        # HL points to the stable descriptor. Length is a word at offset two.
        emit("inc hl", "inc hl", "ld e,(hl)", "inc hl", "ld d,(hl)", "ex de,hl")

    def expression(item):
        nonlocal modulo_used, iterator_new_used
        if item.get("evaluations"):
            for snapshot in item["evaluations"]:
                value, slot = snapshot["value"], snapshot["slot"]
                expression(value)
                if managed(slot["type"]):
                    if not value.get("owned"):
                        emit("push hl", "call __arr_retain", "pop de")
                    emit("push hl")
                    load(slot)
                    emit("push hl", f"call {release(slot['type'])}", "pop de", "pop hl")
                store(slot)
            expression({k: v for k, v in item.items() if k != "evaluations"})
            emit("push hl")
            for snapshot in item["evaluations"]:
                slot = snapshot["slot"]
                if managed(slot["type"]):
                    load(slot)
                    emit(
                        "push hl", f"call {release(slot['type'])}", "pop de", "ld hl,0"
                    )
                    store(slot)
            emit("pop hl")
            return
        kind = item["kind"]
        if kind == "truth":
            runtime(ensure_truth(item["arguments"][0]["type"]), item["arguments"], "")
        elif kind == "iterator_aggregate":
            t = item["base"]["type"]
            key = (t, item["name"], item["type"])
            if key not in aggregate_helpers:
                from .aggregate_runtime import routine

                name = f"__obj_aggregate_{len(aggregate_helpers)}"
                aggregate_helpers[key] = name
                iterator_code.append(
                    routine(
                        t,
                        item["name"],
                        item["type"],
                        name,
                        ensure_iterator(t),
                        ensure_truth(iterator_types(t)[0]),
                    )
                )
            runtime(aggregate_helpers[key], [item["base"], item["start"]], "")
        elif kind == "zero_value":
            emit("ld hl,0")
        elif kind == "bound_argument":
            runtime("get", [item["base"], item["index"]])
        elif kind == "argument_positional":
            runtime("new", [array_width(item["type"])])
            emit("push hl")
            for segment in item["segments"]:
                runtime(
                    "extend" if segment["star"] else "append",
                    [{"kind": "stack_dict", "type": item["type"]}, segment["value"]],
                )
            emit("pop hl")
        elif kind == "argument_keywords":
            if not item["segments"]:
                emit("ld hl,0")
            else:
                runtime(
                    "new", [1, int(managed(dict_types(item["type"])[1])), 0], "__dict_"
                )
                emit("push hl")
                for segment in item["segments"]:
                    base = {"kind": "stack_dict", "type": item["type"]}
                    if segment["mapping"]:
                        runtime("keyword_merge", [base, segment["value"]], "__dict_")
                    else:
                        runtime(
                            "keyword_add",
                            [
                                base,
                                {
                                    "kind": "string",
                                    "value": segment["name"],
                                    "type": "str",
                                    "owned": True,
                                },
                                segment["value"],
                            ],
                            "__dict_",
                        )
                emit("pop hl")
        elif kind == "argument_bind":
            key = (
                tuple(item["names"]),
                tuple(item["defaults"]),
                item["variadic"],
                item["keywords_used"],
                item["type"],
            )
            if key not in argument_binders:
                from .argument_runtime import binder

                name = f"__obj_bind_call_{len(argument_binders)}"
                argument_binders[key] = name
                iterator_code.append(binder(item, name))
            runtime(argument_binders[key], item["arguments"], "")
        elif kind == "wide_round":
            runtime(
                "i32_round" if item["type"] == "i32" else "i32_round_int",
                item["arguments"],
                "__obj_",
            )
        elif kind == "float_parse":
            runtime("f32_parse", item["arguments"], "__obj_")
        elif kind == "float_round":
            runtime("f32_round", item["arguments"], "__obj_")
        elif kind == "float_number":
            runtime("f32_new", [item["raw"] & 65535, item["raw"] >> 16], "__obj_")
        elif kind == "float_cast":
            runtime(
                "f32_from_integer",
                [
                    item["value"],
                    (
                        2
                        if item["source_type"] == "i32"
                        else 1 if item["source_type"] == "fixed" else 0
                    ),
                ],
                "__obj_",
            )
        elif kind in {"float_int", "float_integer"}:
            runtime(
                "f32_int" if kind == "float_int" else "f32_integer",
                item["arguments"],
                "__obj_",
            )
        elif kind == "float_unary":
            runtime("f32_" + item["name"], [item["value"]], "__obj_")
        elif kind == "float_binary":
            runtime("f32_" + item["name"], [item["left"], item["right"]], "__obj_")
        elif kind == "float_compare":
            runtime("f32_compare", [item["left"], item["right"]], "__obj_")
            ordered, no, end = label(), label(), label()
            emit(
                "ld de,2",
                "or a",
                "sbc hl,de",
                f"jp nz,{ordered}",
                f"ld hl,{int(item['operator']=='!=')}",
                f"jp {end}",
            )
            mark(ordered)
            emit("add hl,de", "ld de,0", "or a", "sbc hl,de")
            comparison_flags(item["operator"], no)
            emit("ld hl,1", f"jp {end}")
            mark(no)
            emit("ld hl,0")
            mark(end)
        elif kind == "wide_number":
            runtime(
                "i32_new",
                [item["value"] & 65535, (item["value"] >> 16) & 65535],
                "__obj_",
            )
        elif kind == "wide_cast":
            runtime("i32_from_int", [item["value"]], "__obj_")
        elif kind == "wide_to_int":
            runtime("read", [item["arguments"][0], 0], "__obj_")
        elif kind == "wide_unary":
            runtime("i32_" + item["name"], [item["value"]], "__obj_")
        elif kind == "wide_binary":
            name = item["name"]
            args = [item["left"], item["right"]]
            if name in {"floor_divide", "modulo"}:
                args.append(int(name == "modulo"))
                name = "divide"
            runtime("i32_" + name, args, "__obj_")
        elif kind == "wide_compare":
            runtime("i32_compare", [item["left"], item["right"]], "__obj_")
            no, end = label(), label()
            emit("ld de,0", "or a", "sbc hl,de")
            comparison_flags(item["operator"], no)
            emit("ld hl,1", f"jp {end}")
            mark(no)
            emit("ld hl,0")
            mark(end)
        elif kind == "none":
            emit("ld hl,0")
        elif kind == "optional_box":
            v = item["value"]
            if v["kind"] == "none":
                emit("ld hl,0")
            else:
                expression(
                    {
                        "kind": "tuple",
                        "type": "tuple[" + v["type"] + "]",
                        "values": [v],
                        "owned": True,
                    }
                )
        elif kind == "optional_get":
            runtime("optional_get", [item["value"]], "__obj_")
        elif kind == "identity":
            expression(item["left"])
            emit("push hl")
            expression(item["right"])
            emit("ex de,hl", "pop hl", "or a", "sbc hl,de")
            no, end = label(), label()
            emit(f"jp {'z' if item['negate'] else 'nz'},{no}", "ld hl,1", f"jp {end}")
            mark(no)
            emit("ld hl,0")
            mark(end)
        elif kind == "void_constant":
            return
        elif kind in {"stack_dict", "unpack_source"}:
            emit("pop hl", "push hl")
        elif kind == "dict":
            key, value = dict_types(item["type"])
            runtime(
                "new",
                [int(key == "str"), int(managed(value)), len(item["entries"])],
                "__dict_",
            )
            emit("push hl")
            for entry in item["entries"]:
                runtime(
                    "set",
                    [
                        {"kind": "stack_dict", "type": item["type"]},
                        entry["key"],
                        entry["value"],
                    ],
                    "__dict_",
                )
            emit("pop hl")
        elif kind == "set":
            runtime(
                "new",
                [int(element_type(item["type"]) == "str"), 0, len(item["values"])],
                "__dict_",
            )
            emit("push hl")
            for value in item["values"]:
                runtime(
                    "set",
                    [{"kind": "stack_dict", "type": item["type"]}, value, 0],
                    "__dict_",
                )
            emit("pop hl")
        elif kind == "dict_view_expr":
            runtime(
                "view",
                [item["base"], {"keys": 0, "values": 1, "items": 2}[item["name"]]],
                "__dict_",
            )
        elif kind == "view_target":
            runtime("view_target", [item["base"]], "__dict_")
        elif kind == "view_length":
            runtime("view_len", item["arguments"], "__dict_")
        elif kind == "view_contains":
            runtime("view_contains", [item["left"], item["right"]], "__dict_")
            if item["negate"]:
                emit("ld a,l", "xor 1", "ld l,a")
        elif kind == "dict_list":
            t = item["mapping_type"]
            key, value = dict_types(t) if is_dict(t) else (element_type(t), "i16")
            flags = tuple(int(managed(v)) for v in (key, value))
            if flags not in constant_data:
                constant_data[flags] = f"__constant_data_{len(constant_data)}"
            runtime(
                "materialize",
                [
                    item["base"],
                    {"keys": 0, "values": 1, "items": 2}[item["mode"]],
                    array_width(item["type"]),
                    {"kind": "address", "label": constant_data[flags]},
                ],
                "__dict_",
            )
        elif kind == "set_from":
            runtime(
                "from_sequence",
                [item["base"], int(element_type(item["type"]) == "str")],
                "__set_",
            )
        elif kind == "set_method":
            name, args, base = item["name"], item["arguments"], item["base"]
            if name in {"copy", "clear"}:
                runtime(name, [base], "__dict_")
            elif name in {"issubset", "issuperset", "isdisjoint"}:
                runtime(name, [base, args[0], 0], "__set_")
            elif name in {
                "union",
                "intersection",
                "difference",
                "symmetric_difference",
            }:
                if not args:
                    runtime("copy", [base], "__dict_")
                else:
                    runtime(name, [base, args[0]], "__set_")
                    for arg in args[1:]:
                        emit("push hl")
                        runtime(
                            name,
                            [
                                {
                                    "kind": "stack_dict",
                                    "type": item["type"],
                                    "owned": True,
                                },
                                arg,
                            ],
                            "__set_",
                        )
                        emit("pop de")
            elif name in {
                "update",
                "intersection_update",
                "difference_update",
                "symmetric_difference_update",
            }:
                for arg in args:
                    runtime(name, [base, arg], "__set_")
                emit("ld hl,0")
            else:
                runtime(name, [base, *args], "__set_")
        elif kind in {"set_binary", "dict_merge"}:
            runtime(
                "merge" if kind == "dict_merge" else item["name"],
                [item["left"], item["right"]],
                "__dict_" if kind == "dict_merge" else "__set_",
            )
        elif kind == "view_compare":
            op = item["operator"]
            args = [item["left"], item["right"]]
            if item["name"] == "keys":
                if op in {"==", "!="}:
                    runtime("keys_equal", args, "__set_")
                else:
                    runtime(
                        "issubset" if op in {"<", "<="} else "issuperset",
                        [*args, int(op in {"<", ">"})],
                        "__set_",
                    )
            else:
                if op in {">", ">="}:
                    args.reverse()
                runtime(
                    "items_equal" if op in {"==", "!="} else "items_subset",
                    args if op in {"==", "!="} else [*args, int(op in {"<", ">"})],
                    "__dict_",
                )
            if op == "!=":
                emit("ld a,l", "xor 1", "ld l,a")
        elif kind == "set_compare":
            op = item["operator"]
            if op in {"==", "!="}:
                runtime("equal", [item["left"], item["right"]], "__obj_")
                if op == "!=":
                    emit("ld a,l", "xor 1", "ld l,a")
            else:
                runtime(
                    "issubset" if op in {"<", "<="} else "issuperset",
                    [item["left"], item["right"], int(op in {"<", ">"})],
                    "__set_",
                )
        elif kind in {"iterator_new", "iterator_map"}:
            iterator_new_used = True
            if kind == "iterator_new":
                mode = iterator_types(item["type"])[1]
                runtime(
                    "iterator_new",
                    [
                        item["base"],
                        int(item["reverse"]),
                        0,
                        int(mode in {"keys", "values", "items"}),
                    ],
                    "__obj_",
                )
            else:
                sources = item["sources"]
                source = {
                    "kind": "tuple",
                    "type": "tuple[" + ",".join(v["type"] for v in sources) + "]",
                    "values": sources,
                    "owned": True,
                }
                runtime("iterator_new", [source, 0, item["base"], 0], "__obj_")
        elif kind == "iterator_next":
            args = item["arguments"]
            runtime(ensure_iterator(args[0]["type"]), [args[0]], "")
            good, end = label(), label()
            emit("ld de,(__iterator_exhausted)", "ld a,d", "or e", f"jp z,{good}")
            if len(args) == 1:
                emit("ld hl,16", "jp __arr_fail")
            else:
                expression(args[1])
                if managed(item["type"]) and not args[1].get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
            emit(f"jp {end}")
            mark(good)
            mark(end)
        elif kind == "iterator_list":
            t = item["base"]["type"]
            key = (t, item.get("limit"))
            if key not in iterator_lists:
                from .iterator_runtime import list_routine

                name = f"__obj_iterator_list_{len(iterator_lists)}"
                iterator_lists[key] = name
                iterator_code.append(
                    list_routine(t, name, ensure_iterator(t), item.get("limit"))
                )
            runtime(iterator_lists[key], [item["base"]], "")
        elif kind == "closure_default":
            runtime("read", [item["base"], 1], "__obj_")
            emit(
                "push hl",
                f"ld hl,{item['index']}",
                "push hl",
                "call __obj_read",
                "pop de",
                "pop de",
                "push hl",
                "ld hl,0",
                "push hl",
                "call __arr_get",
                "pop de",
                "pop de",
            )
        elif kind == "checked_value":
            expression(item["value"])
        elif kind == "object_get":
            runtime("get", [item["base"], item["index"]])
        elif kind == "bound_method":
            expression(
                {
                    "kind": "tuple",
                    "type": "tuple[i16," + item["base"]["type"] + "]",
                    "values": [
                        {"kind": "address", "label": item["symbol"], "type": "i16"},
                        item["base"],
                    ],
                    "owned": True,
                }
            )
        elif kind in {"closure", "function_ref"}:
            captures = item.get("captures", [])
            env = {
                "kind": "tuple",
                "values": captures,
                "type": "tuple[" + ",".join(v["type"] for v in captures) + "]",
                "owned": True,
            }
            record = {
                "kind": "tuple",
                "values": [
                    {"kind": "address", "label": item["symbol"], "type": "i16"},
                    env,
                ],
                "type": "tuple[i16,tuple[]]",
                "owned": True,
            }
            expression(record)
        elif kind == "cell_ref":
            cell_pointer(item)
        elif kind == "tuple":
            flags = tuple(int(managed(t)) for t in tuple_types(item["type"]))
            if flags not in constant_data:
                constant_data[flags] = f"__constant_data_{len(constant_data)}"
            runtime(
                "tuple_new",
                [{"kind": "address", "label": constant_data[flags]}, len(flags)],
            )
            emit("push hl")
            for i, value in enumerate(item["values"]):
                runtime(
                    "tuple_set",
                    [{"kind": "stack_dict", "type": item["type"]}, i, value],
                )
            emit("pop hl")
        elif kind == "hash":
            argument = item["arguments"][0]
            if argument["type"] == "str":
                runtime("hash_str", [argument], "__dict_")
            else:
                expression(argument)
        elif kind == "sequence_binary":
            runtime(item["name"], [item["left"], item["right"]])
        elif kind == "sequence_builtin":
            runtime(item["name"], item["arguments"])
        elif kind == "slice":
            runtime(
                "slice",
                [
                    item["base"],
                    item["start"] or 0,
                    item["stop"] or 0,
                    item["step"] or 1,
                    int(item["start"] is None),
                    int(item["stop"] is None),
                ],
            )
        elif kind == "collection_compare":
            op = item["operator"]
            helper = (
                "different"
                if not item["same_type"]
                else "equal" if op in {"==", "!="} else "compare"
            )
            runtime(helper, [item["left"], item["right"]], "__obj_")
            if op == "!=":
                emit("ld a,l", "xor 1", "ld l,a")
            elif op not in {"==", "!="}:
                no, end = label(), label()
                emit(
                    "ld de,2",
                    "or a",
                    "sbc hl,de",
                    f"jp z,{no}",
                    "add hl,de",
                    "ld de,0",
                    "or a",
                    "sbc hl,de",
                )
                comparison_flags(op, no)
                emit("ld hl,1", f"jp {end}")
                mark(no)
                emit("ld hl,0")
                mark(end)
        elif kind == "dict_index":
            runtime("get", [item["base"], item["index"]], "__dict_")
        elif kind == "dict_method":
            name = item["name"]
            if name == "get":
                name = "get_default"
            if name == "pop" and len(item["arguments"]) == 2:
                name = "pop_default"
            runtime(name, [item["base"], *item["arguments"]], "__dict_")
        elif kind == "dict_contains":
            runtime("contains", [item["left"], item["right"]], "__dict_")
            if item["negate"]:
                emit("ld a,l", "xor 1", "ld l,a")
        elif kind == "string":
            data = tuple(ord(c) for c in item["value"])
            if data not in constant_data:
                constant_data[data] = f"__constant_data_{len(constant_data)}"
            runtime(
                "from_data",
                [1, len(data), {"kind": "address", "label": constant_data[data]}],
            )
        elif kind == "fstring":
            runtime("new", [1])
            emit("push hl")
            for value in item["values"]:
                runtime("extend", [{"kind": "stack_dict", "type": "str"}, value])
            emit("pop hl")
        elif kind == "string_builtin":
            runtime(item["name"], item["arguments"], "__str_")
        elif kind == "string_index":
            runtime("get", [item["base"], item["index"]], "__str_")
        elif kind == "string_method":
            runtime(item["name"], [item["base"], *item["arguments"]], "__str_")
        elif kind == "string_contains":
            runtime("contains", [item["left"], item["right"]], "__str_")
            if item["negate"]:
                emit("ld a,l", "xor 1", "ld l,a")
        elif kind in {"text_print", "text_input"}:
            value, x, y = item["arguments"]
            reentrant = may_call_user(x) or may_call_user(y)
            if value["type"] in {"i16", "u8", "bool"} and reentrant:
                value = {
                    "kind": "string_builtin",
                    "name": "boolean" if value["type"] == "bool" else "number",
                    "arguments": [value],
                    "type": "str",
                    "owned": True,
                }
            temporary = value["kind"] != "string" and (
                value.get("owned", False) or value["type"] == "str" and reentrant
            )
            if value["kind"] == "string":
                data = tuple(ord(c) for c in value["value"])
                if data not in constant_data:
                    constant_data[data] = f"__constant_data_{len(constant_data)}"
                emit(
                    f"ld hl,{constant_data[data]}",
                    "push hl",
                    f"ld hl,{len(data)}",
                    "push hl",
                )
            else:
                expression(value)
                if temporary and not value.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                if temporary:
                    emit("push hl")
                if value["type"] == "str":
                    emit(
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "inc hl",
                        "ld c,(hl)",
                        "inc hl",
                        "ld b,(hl)",
                        "push de",
                        "push bc",
                    )
                else:
                    emit(
                        f"call __text_{'bool' if value['type'] == 'bool' else 'number'}",
                        "push hl",
                        "push bc",
                    )
            expression(x)
            emit("push hl")
            expression(y)
            emit("push hl")
            if kind == "text_input":
                emit(
                    f"ld hl,{int(item['type'] == 'i16')}",
                    "push hl",
                    "call __text_input",
                    *(["pop de"] * 5),
                )
            else:
                emit("call __text_draw", *(["pop de"] * 4))
            if temporary:
                emit(
                    "pop de",
                    "push hl",
                    "push de",
                    f"call {release(value['type'])}",
                    "pop de",
                    "pop hl",
                )
        elif kind in {"number", "boolean"}:
            emit(f"ld hl,{int(item['value']) & 0xFFFF}")
        elif kind == "decimal":
            emit(f"ld hl,{item['raw'] & 65535}")
        elif kind == "to_fixed":
            expression(item["value"])
            emit("ld h,l", "ld l,0")
        elif kind == "cast_int":
            argument = item["arguments"][0]
            expression(argument)
            if argument["type"] == "fixed":
                positive = label()
                emit("bit 7,h", f"jp z,{positive}", "ld de,255", "add hl,de")
                mark(positive)
                emit("ld l,h", "ld h,0", "bit 7,l")
                end = label()
                emit(f"jp z,{end}", "dec h")
                mark(end)
        elif kind == "cpu_speed":
            emit(f"ld a,{item['mode']}", "out (32),a")
        elif kind == "key_down":
            expression(item["arguments"][0])
            emit("call __native_key_down")
        elif kind == "stdlib":
            name, args = item["name"], item["arguments"]
            if name in {"time.frame_ticks", "time.stop_frame_timer"}:
                emit("call __native_" + name.split(".")[-1])
            elif name == "random.randint":
                for arg in args:
                    expression(arg)
                    emit("push hl")
                emit("call __native_randint", "pop de", "pop de")
            elif name == "time.sleep":
                if "milliseconds" in item:
                    emit(f"ld hl,{item['milliseconds']}")
                else:
                    expression(args[0])
                    conversion = (
                        "sleep_fixed" if args[0]["type"] == "fixed" else "sleep_integer"
                    )
                    native_used.add(conversion)
                    math_used.add("mul")
                    emit(f"call __native_{conversion}")
                emit("call __native_sleep")
            else:
                expression(args[0])
                if name in {"math.sin", "math.cos"} and args[0]["type"] != "fixed":
                    emit("ld h,l", "ld l,0")
                emit(f"call __native_{name.split('.')[-1]}")
        elif kind == "argument_check":
            expression(item["value"])
        elif kind == "global_value":
            emit(f"ld hl,({item['label']})")
        elif kind == "geometry_data":
            if item.get("face_count"):
                pixels_used.add("mesh_faces")
            if item.get("face_count") and item.get("quad_format"):
                pixels_used.add("solid_quads")
            if item.get("face_centroids"):
                pixels_used.add("solid_centroids")
            data = tuple(item["data"])
            if item.get("compact"):
                if data not in geometry_packets:
                    from .wireframe import vertex_program

                    count = item["vertex_count"]
                    vertices = []
                    for i in range(count):
                        row = []
                        for j in range(3):
                            k = 8 + i * 6 + j * 2
                            value = data[k] | data[k + 1] << 8
                            row.append(value - 65536 if value & 32768 else value)
                        vertices.append(tuple(row))
                    key = tuple(vertices)
                    if key not in vertex_programs:
                        name = f"__gfx_wf_vertices_{len(vertex_programs)}"
                        vertex_programs[key] = (name, vertex_program(vertices, name))
                    geometry_packets[data] = (
                        f"__geometry_{len(geometry_packets)}",
                        vertex_programs[key][0],
                        data[:8] + data[8 + count * 6 :],
                    )
                emit(f"ld hl,{geometry_packets[data][0]}")
            else:
                if data not in constant_data:
                    constant_data[data] = f"__constant_data_{len(constant_data)}"
                if compiled_mesh_vertices and not item.get("collision_map"):
                    from .wireframe import vertex_program

                    vertices = _decode_mesh_vertices(item)
                    key = constant_data[data]
                    if key not in mesh_programs:
                        name = f"__gfx_mesh_vertices_{len(mesh_programs)}"
                        mesh_programs[key] = (
                            name,
                            vertex_program(
                                vertices,
                                name,
                                basis="__gfx_mesh_view",
                                center="__gfx_mesh_center",
                                bounds=(
                                    item.get("hull_bounds")
                                    if blank_mesh_spans
                                    else None
                                ),
                            ),
                        )
                    pixels_used.add("compiled_mesh_vertices")
                emit(f"ld hl,{constant_data[data]}")
            if item["vertex_count"]:
                pixels_used.add("batch_capacity:" + str(item["vertex_count"]))
        elif kind == "address":
            emit(f"ld hl,{item['label']}")
        elif kind == "constant_list":
            width = 2 if item["type"] == "i16[]" else 1
            if not item["values"]:
                runtime("new", [width])
            else:
                values = []
                for value in item["values"]:
                    values.append(int(value) & 255)
                    if width == 2:
                        values.append((int(value) >> 8) & 255)
                key = tuple(values)
                if key not in constant_data:
                    constant_data[key] = f"__constant_data_{len(constant_data)}"
                runtime(
                    "from_data",
                    [
                        width,
                        len(item["values"]),
                        {"kind": "address", "label": constant_data[key]},
                    ],
                )
        elif kind == "name":
            load(item)
        elif kind == "list":

            def literal(value):
                if value["kind"] in {"number", "boolean"}:
                    return int(value["value"])
                if value["kind"] == "negate" and value["value"]["kind"] == "number":
                    return -value["value"]["value"]
                return None

            scalars = [literal(value) for value in item["values"]]
            if (
                len(scalars) >= 16
                and item["type"] in {"i16[]", "u8[]", "bool[]"}
                and all(value is not None for value in scalars)
            ):
                expression(
                    {"kind": "constant_list", "type": item["type"], "values": scalars}
                )
                return
            width = array_width(item["type"])
            runtime("new", [width])
            emit("push hl")
            for value in item["values"]:
                emit("pop hl", "push hl", "push hl")
                expression(value)
                emit("push hl", "call __arr_append", "pop de")
                if value.get("owned"):
                    emit("push de", f"call {release(value['type'])}", "pop de")
                emit("pop de")
            emit("pop hl")
        elif kind == "conditional":
            other, end = label(), label()
            branch_false(item["condition"], other)
            for index, value in enumerate((item["body"], item["alternative"])):
                if index:
                    mark(other)
                expression(value)
                if managed(item["type"]) and not value.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                if not index:
                    emit(f"jp {end}")
            mark(end)
        elif kind == "comprehension":
            collection = item.get("collection", "list")
            if collection == "list":
                runtime("new", [array_width(item["type"])])
            else:
                key, value = (
                    dict_types(item["type"])
                    if collection == "dict"
                    else (element_type(item["type"]), "i16")
                )
                runtime("new", [int(key == "str"), int(managed(value)), 0], "__dict_")
            emit("push hl")
            statements([item["loop"]])
            emit("pop hl")
        elif kind == "length":
            argument = item["arguments"][0]
            expression(argument)
            if argument.get("owned"):
                emit("push hl")
            read_length()
            if argument.get("owned"):
                emit(
                    "pop de",
                    "push hl",
                    "push de",
                    f"call {release(argument['type'])}",
                    "pop de",
                    "pop hl",
                )
        elif kind == "builtin":
            name, arguments = item["name"], item["arguments"]
            if name == "range":
                values = (
                    [0, arguments[0], 1]
                    if len(arguments) == 1
                    else [*arguments, 1] if len(arguments) == 2 else arguments
                )
                runtime("range", values)
            elif name == "list":
                if not arguments:
                    runtime("new", [2 if item["type"] == "i16[]" else 1])
                elif (
                    arguments[0]["kind"] in {"list", "comprehension"}
                    or arguments[0].get("name") == "range"
                    and arguments[0]["kind"] == "builtin"
                ):
                    expression(arguments[0])
                else:
                    runtime("copy", arguments)
            elif name == "abs":
                expression(arguments[0])
                end = label()
                emit(
                    "bit 7,h",
                    f"jp z,{end}",
                    "ld a,l",
                    "cpl",
                    "ld l,a",
                    "ld a,h",
                    "cpl",
                    "ld h,a",
                    "inc hl",
                )
                mark(end)
            elif item.get("scalar"):
                expression(arguments[0])
                for argument in arguments[1:]:
                    emit("push hl")
                    expression(argument)
                    emit(
                        "ex de,hl",
                        "pop hl",
                        "push hl",
                        "push de",
                        "ld a,h",
                        "xor 128",
                        "ld h,a",
                        "ld a,d",
                        "xor 128",
                        "ld d,a",
                        "or a",
                        "sbc hl,de",
                        "pop de",
                        "pop hl",
                    )
                    keep = label()
                    emit(f"jp {'c' if name == 'min' else 'nc'},{keep}", "ex de,hl")
                    mark(keep)
            else:
                runtime(
                    name,
                    (
                        [*arguments, 0]
                        if name == "sum" and len(arguments) == 1
                        else arguments
                    ),
                )
        elif kind == "index":
            runtime("get", [item["base"], item["index"]])
        elif kind == "method":
            arguments = [item["base"], *item["arguments"]]
            if item["name"] == "pop" and not item["arguments"]:
                arguments.append(-1)
            runtime(item["name"], arguments)
        elif kind == "native_graphics":
            if "light_direction_constants" in item:
                from .camera_api import mesh_unit_vector

                values = item["light_direction_constants"]
                runtime(
                    "solid_directional_coeff",
                    mesh_unit_vector(values[:3], approximate_mesh) + [values[3]],
                    "__gfx_",
                )
                pixels_used.add("solid_directional_coeff")
            elif "camera_constants" in item:
                good = label()
                emit(
                    "ld hl,(__gfx_cam_path_mode)",
                    "ld a,h",
                    "or l",
                    f"jp z,{good}",
                    "ld hl,14",
                    "jp __arr_fail",
                )
                mark(good)
                constants = item["camera_constants"]
                for key in ("focal", "near", "far"):
                    if key in constants:
                        emit(
                            f"ld hl,{constants[key]&65535}", f"ld (__gfx_cam_{key}),hl"
                        )
                        if key != "focal":
                            emit(
                                f"ld hl,{(constants[key]>>16)&65535}",
                                f"ld (__gfx_cam_{key}+2),hl",
                            )
                for key in ("eye", "basis"):
                    for i, value in enumerate(constants.get(key, [])):
                        emit(f"ld hl,{value&65535}", f"ld (__gfx_cam_{key}+{i*2}),hl")
                emit("ld hl,1", "ld (__gfx_cam_enabled),hl")
                pixels_used.add("cam_state")
            else:
                if blank_mesh_spans and item["name"] == "mesh_draw":
                    emit(
                        f"ld a,{int(item.get('blank_framebuffer',False))}",
                        "ld (__gfx_mesh_blank),a",
                    )
                    pixels_used.add("mesh_blank_spans")
                runtime(item["name"], item["arguments"], "__gfx_")
                pixels_used.add(item["name"])
        elif kind == "native_matrix":
            if "rotation_constants" in item:
                for value in item["rotation_constants"]:
                    emit(
                        f"ld hl,{value&65535}",
                        "push hl",
                        f"ld hl,{(value>>16)&65535}",
                        "push hl",
                    )
                emit(
                    f"ld hl,{item['axis']}",
                    "push hl",
                    "call __gfx_mat_rotate_coeff",
                    *(["pop bc"] * 5),
                )
                pixels_used.add("mat_rotate_coeff")
                return
            for arg in item["arguments"]:
                expression(arg)
                if arg["type"] in {"i16", "u8"}:
                    emit("ex de,hl", "ld hl,0")
                elif arg["type"] == "fixed":
                    positive = label()
                    emit(
                        "ld e,h",
                        "ld h,l",
                        "ld l,0",
                        "ld d,0",
                        "bit 7,e",
                        f"jp z,{positive}",
                        "ld d,255",
                    )
                    mark(positive)
                elif arg["type"] == "float":
                    emit("push hl", "call __gfx_mat_from_float", "pop bc")
                    pixels_used.add("mat_from_float")
                else:
                    negative, good, bad = label(), label(), label()
                    emit(
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "ex de,hl",
                        "ld c,(hl)",
                        "inc hl",
                        "ld b,(hl)",
                        "inc hl",
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "bit 7,b",
                        f"jp nz,{negative}",
                        "ld a,d",
                        "or e",
                        f"jp z,{good}",
                        f"jp {bad}",
                    )
                    mark(negative)
                    emit("ld a,d", "and e", "cp 255", f"jp z,{good}")
                    mark(bad)
                    emit("ld hl,14", "jp __arr_fail")
                    mark(good)
                    emit("ld d,b", "ld e,c", "ld hl,0")
                emit("push hl", "push de")
            if "axis" in item:
                emit(f"ld hl,{item['axis']}", "push hl")
                modulo_used = True
            emit(
                f"call __gfx_{item['name']}",
                *(["pop bc"] * (len(item["arguments"]) * 2 + int("axis" in item))),
            )
            pixels_used.add(item["name"])
        elif kind == "raw_fixed":
            expression(item["value"])
        elif kind == "graphics_value":
            if item["name"] == "sin_raw":
                modulo_used = True
            runtime(item["name"], item["arguments"], "__gfx_")
            pixels_used.add(item["name"])
        elif kind == "graphics":
            if item["name"] == "ink":
                expression(item["arguments"][0])
                emit("ld a,l", "ld (__gfx_ink),a")
                pixels_used.add("ink")
                return
            if item["name"] == "triangle":
                args = item["arguments"]
                points = {
                    "kind": "list",
                    "type": "tuple[i16,i16][]",
                    "owned": True,
                    "values": [
                        {
                            "kind": "tuple",
                            "type": "tuple[i16,i16]",
                            "owned": True,
                            "values": args[i : i + 2],
                        }
                        for i in (0, 2, 4)
                    ],
                }
                runtime("polygon", [points, args[6]], "__gfx_")
                pixels_used.add("polygon")
                return
            if item["name"] in {"circle", "ellipse", "polygon", "polyline", "stroke"}:
                runtime(item["name"], item["arguments"], "__gfx_")
                pixels_used.add(item["name"])
                return
            if item["name"] in {"pixel_on", "pixel_off", "flip_pixel"}:
                expression(item["arguments"][0])
                emit("push hl")
                expression(item["arguments"][1])
                emit("ex de,hl", "pop hl", f"call __gfx_{item['name']}")
            else:
                for argument in item["arguments"]:
                    expression(argument)
                    emit("push hl")
                emit(f"call __gfx_{item['name']}")
                emit(*(["pop de"] * len(item["arguments"])))
            pixels_used.add(item["name"])
        elif kind == "call" and item.get("constructor"):
            fields = item["field_types"]
            flags = tuple(int(managed(t)) for t in fields)
            if flags not in constant_data:
                constant_data[flags] = f"__constant_data_{len(constant_data)}"
            runtime(
                "tuple_new",
                [{"kind": "address", "label": constant_data[flags]}, len(fields)],
            )
            emit(
                "push hl",
                "ld de,6",
                "add hl,de",
                "ld (hl),0",
                "inc hl",
                "ld (hl),128",
                "pop hl",
            )
            emit("push hl", "push hl", "call __arr_retain", "pop de", "push hl")
            for arg in item["arguments"]:
                expression(arg)
                if managed(arg["type"]) and not arg.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
            emit(
                f"call {item['symbol']}",
                *(["pop de"] * (len(item["arguments"]) + 1)),
                "pop hl",
            )
        elif kind in {"call", "closure_call"} and item.get("generator"):
            function = functions[item["symbol"]]
            values = item["arguments"][:]
            if item["kind"] == "closure_call" and item["has_env"]:
                values.insert(
                    0,
                    {
                        "kind": "generator_env",
                        "base": item["base"],
                        "type": function["params"][0]["type"],
                    },
                )
            params = {
                "kind": "tuple",
                "type": "tuple[" + ",".join(v["type"] for v in values) + "]",
                "values": values,
                "owned": True,
            }
            runtime(
                "gen_new",
                [
                    {"kind": "address", "label": item["symbol"]},
                    params,
                    function["frame_size"],
                ],
                "__obj_",
            )
        elif kind == "generator_env":
            runtime("read", [item["base"], 1], "__obj_")
        elif kind == "closure_call":
            expression(item["base"])
            emit("push hl", "call __arr_retain", "pop de", "push hl")
            if item["has_env"]:
                emit(
                    "push hl",
                    "ld hl,1",
                    "push hl",
                    "call __obj_read",
                    "pop de",
                    "pop de",
                    "push hl",
                    "call __arr_retain",
                    "pop de",
                    "push hl",
                )
            for argument in item["arguments"]:
                expression(argument)
                if managed(argument["type"]) and not argument.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
            emit(f"call {item['symbol']}")
            emit(*(["pop de"] * (len(item["arguments"]) + int(item["has_env"]))))
            emit(
                "pop de", "push hl", "push de", "call __arr_release", "pop de", "pop hl"
            )
        elif kind == "call":
            # Arguments are evaluated left-to-right and passed as word copies.
            # Nested calls preserve IX and all expression temporaries on stack.
            for argument in item["arguments"]:
                expression(argument)
                if managed(str(argument["type"])) and not argument.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
            emit(f"call {item['symbol']}")
            for _ in item["arguments"]:
                emit("pop de")  # Caller cleanup, preserving the result in HL.
        elif kind == "membership":
            runtime("contains", [item["left"], item["right"]])
            if item["negate"]:
                emit("ld a,l", "xor 1", "ld l,a")
        elif kind == "not":
            expression(item["value"])
            emit("ld a,l", "xor 1", "ld l,a", "ld h,0")
        elif kind == "numeric_builtin":
            if item["name"] == "identity_number":
                expression(item["arguments"][0])
            else:
                if item["name"] == "pow_mod":
                    modulo_used = True
                runtime(item["name"], item["arguments"], "__obj_")
        elif kind == "true_divide":
            runtime(
                "i32_fixed_divide",
                [item["left"], item["right"], item["shift"]],
                "__obj_",
            )
        elif kind == "invert":
            expression(item["value"])
            emit("ld a,h", "cpl", "ld h,a", "ld a,l", "cpl", "ld l,a")
        elif kind in {"shift_left", "shift_right", "power"}:
            runtime(kind, [item["left"], item["right"]], "__obj_")
        elif kind in {"bit_and", "bit_or", "bit_xor"}:
            if item["right"].get("kind") == "number" and kind == "bit_and" and 0<=item["right"]["value"]<=255:
                expression(item["left"])
                mask=item["right"]["value"]
                if mask!=255:emit("ld a,l",f"and {mask}","ld l,a")
                emit("ld h,0")
                return
            expression(item["left"])
            emit("push hl")
            expression(item["right"])
            emit("ex de,hl", "pop hl")
            op = {"bit_and": "and", "bit_or": "or", "bit_xor": "xor"}[kind]
            emit("ld a,l", f"{op} e", "ld l,a", "ld a,h", f"{op} d", "ld h,a")
        elif kind == "negate":
            expression(item["value"])
            emit("ex de,hl", "ld hl,0", "or a", "sbc hl,de")
        elif kind in {"compare", "compare_chain", "and", "or"}:
            false, end = label(), label()
            branch_false(item, false)
            emit("ld hl,1", f"jp {end}")
            mark(false)
            emit("ld hl,0")
            mark(end)
        elif kind == "modulo":
            expression(item["left"])
            right = item["right"]
            divisor = right["value"] if right["kind"] == "number" else 0
            if 0 < divisor <= 256 and divisor & (divisor - 1) == 0:
                emit("ld a,l", f"and {divisor - 1}", "ld l,a", "ld h,0")
            else:
                emit("push hl")
                expression(right)
                emit("ex de,hl", "pop hl", "call __math_mod")
                modulo_used = True
        else:
            expression(item["left"])
            emit("push hl")
            expression(item["right"])
            emit("ex de,hl", "pop hl")
            if kind in {"multiply", "floor_divide"}:
                helper = "mul" if kind == "multiply" else "div"
                math_used.add(helper)
                emit(f"call __math_{helper}")
            elif kind == "add":
                emit("add hl,de")
            else:
                emit("or a", "sbc hl,de")

    def branch_false(item, false):
        inverted = item["kind"] == "not"
        tested = item["value"] if inverted else item
        if tested.get("kind") == "name" and tested.get("type") in {"bool","u8"} and not tested.get("cell"):
            if tested.get("storage") == "frame":emit(f"ld a,(ix{tested['offset']:+d})")
            else:emit(f"ld a,({tested['symbol']})")
            emit("or a",f"jp {'nz' if inverted else 'z'},{false}")
            return
        if inverted:
            expression(tested)
            emit("ld a,h","or l",f"jp nz,{false}")
            return
        if item["kind"] == "compare_chain":
            expression(item["operands"][0])
            for i, (operator, right) in enumerate(
                zip(item["operators"], item["operands"][1:])
            ):
                more = i < len(item["operators"]) - 1
                fail, success = label(), label()
                emit("push hl")
                expression(right)
                emit("ex de,hl", "pop hl")
                if more:
                    emit("push de")
                emit("or a", "sbc hl,de")
                comparison_flags(operator, fail if more else false)
                if more:
                    emit("pop hl", f"jp {success}")
                    mark(fail)
                    emit("pop de", f"jp {false}")
                    mark(success)
            return
        if item["kind"] == "and":
            branch_false(item["left"], false)
            branch_false(item["right"], false)
            return
        if item["kind"] == "or":
            right, end = label(), label()
            branch_false(item["left"], right)
            emit(f"jp {end}")
            mark(right)
            branch_false(item["right"], false)
            mark(end)
            return
        if item["kind"] != "compare":
            expression(item)
            emit("ld a,h", "or l", f"jp z,{false}")
            return
        op = item["operator"]
        left, right = item["left"], item["right"]
        if op in {"<", ">="} and right.get("kind") == "number" and right["value"] == 0:
            expression(left)
            emit("bit 7,h",f"jp {'z' if op == '<' else 'nz'},{false}")
            return
        if op in {"==", "!="} and right["kind"] == "number" and right["value"] == 0:
            if left["kind"] == "modulo" and left["right"]["kind"] == "number":
                divisor = left["right"]["value"]
                if 0 < divisor <= 256 and divisor & (divisor - 1) == 0:
                    expression(left["left"])
                    emit(
                        "ld a,l",
                        f"and {divisor - 1}",
                        f"jp {'nz' if op == '==' else 'z'},{false}",
                    )
                    return
            expression(left)
            emit("ld a,h", "or l", f"jp {'nz' if op == '==' else 'z'},{false}")
            return
        expression(item["left"])
        emit("push hl")
        expression(item["right"])
        emit("ex de,hl", "pop hl", "or a", "sbc hl,de")
        comparison_flags(op, false)

    def comparison_flags(op, false):
        if op in {"==", "!="}:
            emit(f"jp {'nz' if op == '==' else 'z'},{false}")
            return
        # Signed ordering: S XOR V means less-than. Z handles strict/inclusive
        # equality. JP does not change these flags between tests.
        true, overflow = label(), label()
        if op == ">":
            emit(f"jp z,{false}")
        elif op == "<=":
            emit(f"jp z,{true}")
        negative_is_true = op in {"<", "<="}
        emit(
            f"jp pe,{overflow}",
            f"jp {'m' if negative_is_true else 'p'},{true}",
            f"jp {false}",
        )
        mark(overflow)
        emit(f"jp {'p' if negative_is_true else 'm'},{true}", f"jp {false}")
        mark(true)

    def enter_handler(target):
        emit(
            "ld hl,0",
            "add hl,sp",
            "push ix",
            "push hl",
            f"ld hl,{target}",
            "push hl",
            "ld hl,(__exception_handler)",
            "push hl",
            "ld hl,0",
            "add hl,sp",
            "ld (__exception_handler),hl",
        )

    def leave_handler():
        emit(
            "ld hl,(__exception_handler)",
            "ld e,(hl)",
            "inc hl",
            "ld d,(hl)",
            "ld (__exception_handler),de",
            "inc hl",
            "inc hl",
            "inc hl",
            "ld e,(hl)",
            "inc hl",
            "ld d,(hl)",
            "ex de,hl",
            "ld sp,hl",
        )

    def clear_abandoned(body):
        slots = {}
        for node in walk(body):
            for evaluation in node.get("evaluations", []):
                slot = evaluation["slot"]
                if managed(slot["type"]):
                    slots[slot["symbol"]] = slot
            for key, slot in node.items():
                if (
                    (key.startswith("iterator_") or key == "completion_slot")
                    and isinstance(slot, dict)
                    and "symbol" in slot
                    and managed(slot.get("type", ""))
                ):
                    slots[slot["symbol"]] = slot
        for slot in slots.values():
            emit("ld hl,0")
            store(slot)

    def unwind_to(depth):
        saved = unwind[:]
        for i in reversed(range(depth, len(saved))):
            leave_handler()
            unwind[:] = saved[:i]
            if saved[i]:
                statements(saved[i])
        unwind[:] = saved

    def unpack_pattern(pattern):
        if pattern["kind"] == "target_name":
            if managed(pattern["type"]) and not pattern.get("cell"):
                emit("push hl")
                load(pattern)
                emit("push hl", f"call {release(pattern['type'])}", "pop de", "pop hl")
            store(pattern)
            return
        if pattern.get("conversion"):
            emit("push hl")
            expression(pattern["conversion"])
            emit(
                "pop de",
                "push hl",
                "push de",
                f"call {release(pattern['source_type'])}",
                "pop de",
                "pop hl",
            )
        emit("push hl")
        read_length()
        emit(f"ld de,{pattern['minimum']}", "or a", "sbc hl,de")
        valid = label()
        emit(
            f"jp {'nc' if pattern['star']>=0 else 'z'},{valid}",
            "ld hl,14",
            "jp __arr_fail",
        )
        mark(valid)
        for i, child in enumerate(pattern["values"]):
            source = {"kind": "stack_dict", "type": pattern["type"]}
            if i == pattern["star"]:
                runtime(
                    "unpack_rest",
                    [
                        source,
                        i,
                        len(pattern["values"]) - i - 1,
                        array_width(child["type"]),
                        int(pattern["type"] == "str"),
                    ],
                    "__obj_",
                )
                unpack_pattern(child["value"])
            else:
                runtime(
                    "get",
                    [source, child["unpack_index"]],
                    "__str_" if pattern["type"] == "str" else "__arr_",
                )
                unpack_pattern(child)
        emit("pop hl", "push hl", f"call {release(pattern['type'])}", "pop de")

    def statements(body):
        nonlocal modulo_used
        for statement in body:
            kind = statement["kind"]
            if statement.get("unreachable") or kind == "function":
                continue
            code.append(
                f"; Line {statement['line']}: {kind} {statement.get('name', '')}"
            )
            if kind == "pixel_batch":
                for start, count, keep, bits in statement["operations"]:
                    emit(f"ld hl,{0x9340+start}")
                    if keep == 0:
                        emit(f"ld (hl),{bits}")
                        if count > 1:
                            emit(f"ld de,{0x9341+start}", f"ld bc,{count-1}", "ldir")
                    else:
                        while count:
                            chunk = min(count, 255)
                            again = label()
                            emit(f"ld b,{chunk}")
                            mark(again)
                            emit("ld a,(hl)")
                            if keep != 255:
                                emit(f"and {keep}")
                            if bits:
                                emit(f"xor {bits}")
                            emit("ld (hl),a", "inc hl", f"djnz {again}")
                            count -= chunk
                for final in statement["finals"]:
                    emit(f"ld hl,{int(final['value'])&65535}")
                    store(final["target"])
                continue
            if kind == "release_temp":
                if managed(statement["type"]):
                    load(statement)
                    emit(
                        "push hl",
                        f"call {release(statement['type'])}",
                        "pop de",
                        "ld hl,0",
                    )
                    store(statement)
                continue
            if kind == "pattern_assign":
                value = statement["value"]
                expression(value)
                if managed(value["type"]) and not value.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                unpack_pattern(statement["target"])
                continue
            if kind == "yield":
                expression(statement["value"])
                emit("call __obj_gen_yield")
                continue
            if kind in {"try_except", "try_finally"}:
                caught, end = label(), label()
                enter_handler(caught)
                unwind.append(statement.get("final"))
                statements(statement["body"])
                unwind.pop()
                leave_handler()
                if kind == "try_finally":
                    statements(statement["final"])
                    emit(f"jp {end}")
                    mark(caught)
                    clear_abandoned(statement["body"])
                    emit("ld hl,(__exception_code)")
                    store(statement["code_slot"])
                    statements(statement["final"])
                    load(statement["code_slot"])
                    emit("jp __obj_throw")
                else:
                    statements(statement["alternative"])
                    emit(f"jp {end}")
                    mark(caught)
                    clear_abandoned(statement["body"])
                    from .exception_runtime import EXCEPTIONS

                    for handler in statement["handlers"]:
                        yes, no = label(), label()
                        if handler["types"] and not any(
                            t in {"Exception", "BaseException"}
                            for t in handler["types"]
                        ):
                            for code_value in {
                                c for t in handler["types"] for c in EXCEPTIONS[t]
                            }:
                                emit(
                                    "ld hl,(__exception_code)",
                                    f"ld de,{code_value}",
                                    "or a",
                                    "sbc hl,de",
                                    f"jp z,{yes}",
                                )
                            emit(f"jp {no}")
                        mark(yes)
                        emit("ld hl,(__exception_code)")
                        store(handler["code_slot"])
                        if "target" in handler:
                            exception = {
                                "kind": "tuple",
                                "type": "tuple[i16,str]",
                                "values": [
                                    {**handler["code_slot"], "kind": "name"},
                                    {
                                        "kind": "global_value",
                                        "label": "__exception_message",
                                        "type": "str",
                                    },
                                ],
                            }
                            expression(exception)
                            store(handler["target"])
                        statements(handler["body"])
                        emit(f"jp {end}")
                        mark(no)
                    emit("ld hl,(__exception_code)", "jp __obj_throw")
                mark(end)
                continue
            if kind in {"assert", "raise"}:
                success = label()
                if kind == "assert":
                    expression(statement["value"])
                    emit("ld a,h", "or l", f"jp nz,{success}")
                if statement.get("message"):
                    expression(statement["message"])
                else:
                    emit("ld hl,0")
                emit("ld (__exception_message),hl")
                if "exception_code" in statement:
                    emit(f"ld hl,{statement['exception_code']}")
                elif "code_slot" in statement:
                    load(statement["code_slot"])
                else:
                    runtime("read", [statement["value"], 0], "__obj_")
                emit("jp __obj_throw")
                mark(success)
                continue
            if kind == "pass":
                continue
            if kind == "expression_statement":
                expression(statement["value"])
                if statement["value"].get("owned"):
                    emit(
                        "push hl",
                        f"call {release(statement['value']['type'])}",
                        "pop de",
                    )
                continue
            if kind == "dict_delete":
                target = statement["target"]
                runtime("delete", [target["base"], target["index"]], "__dict_")
                continue
            if kind == "collect":
                if statement.get("collection") in {"dict", "set"}:
                    key = (
                        statement["key"]
                        if statement["collection"] == "dict"
                        else statement["value"]
                    )
                    runtime(
                        "set",
                        [
                            {"kind": "stack_dict", "type": "dict[i16,i16]"},
                            key,
                            (
                                statement["value"]
                                if statement["collection"] == "dict"
                                else 0
                            ),
                        ],
                        "__dict_",
                    )
                    continue
                emit("pop hl", "push hl", "push hl")
                expression(statement["value"])
                emit("push hl", "call __arr_append", "pop de")
                if statement["value"].get("owned"):
                    emit(
                        "push de",
                        f"call {release(statement['value']['type'])}",
                        "pop de",
                    )
                emit("pop de")
                continue
            if kind == "unpack_assign":
                value = statement["value"]
                expression(value)
                if not value.get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
                read_length()
                emit(f"ld de,{len(statement['targets'])}", "or a", "sbc hl,de")
                valid = label()
                emit(f"jp z,{valid}", "ld hl,14", "jp __arr_fail")
                mark(valid)
                for i, target in enumerate(statement["targets"]):
                    runtime("get", [{"kind": "stack_dict", "type": value["type"]}, i])
                    if managed(target["type"]) and not target.get("cell"):
                        emit("push hl")
                        load(target)
                        emit(
                            "push hl",
                            f"call {release(target['type'])}",
                            "pop de",
                            "pop hl",
                        )
                    store(target)
                emit("pop hl", "push hl", f"call {release(value['type'])}", "pop de")
                continue
            if kind == "prepare_row_mask":
                load(statement["source"])
                read_length()
                store(statement["mask"]["length"])
                load(statement["source"])
                emit("push hl")
                buffer_address(statement["mask"]["buffer"])
                emit(
                    "push hl",
                    f"ld hl,{int(statement['parity'])}",
                    "push hl",
                    "call __gfx_build_row_mask",
                    "pop de",
                    "pop de",
                    "pop de",
                )
                store(statement["mask"]["last"])
                pixels_used.add("build_row_mask")
                continue
            if kind == "pixel_row":
                end = label()
                load(statement["mask"]["length"])
                emit("ld a,h", "or l", f"jp z,{end}")
                load(statement["mask"]["last"])
                store(statement["target"])
                load(statement["row"])
                emit(
                    "ld a,h",
                    "or a",
                    f"jp nz,{end}",
                    "ld a,l",
                    "cp 64",
                    f"jp nc,{end}",
                    "add hl,hl",
                    "add hl,hl",
                    "push hl",
                    "add hl,hl",
                    "pop de",
                    "add hl,de",
                    "ld de,0x9340",
                    "add hl,de",
                    "push hl",
                )
                buffer_address(statement["mask"]["buffer"])
                mode = {
                    "pixel_on": "or_row",
                    "pixel_off": "and_row",
                    "flip_pixel": "xor_row",
                }[statement["mode"]]
                emit("ex de,hl", "pop hl", f"call __gfx_{mode}")
                mark(end)
                pixels_used.add(mode)
                continue
            if kind in {"index_assign", "dict_assign", "object_assign"}:
                target = statement["target"]
                expression(target["base"])
                if not target["base"].get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
                expression(target["index"])
                if managed(target["index"]["type"]) and not target["index"].get(
                    "owned"
                ):
                    emit("push hl", "call __arr_retain", "pop de")
                emit("push hl")
                if statement["operator"] != "=":
                    # Preserve the original descriptor/index, and evaluate the
                    # index exactly once even if it contains a function call.
                    emit(
                        "pop de",
                        "pop hl",
                        "push hl",
                        "push de",
                        "push hl",
                        "push de",
                        f"call {'__dict_get' if kind == 'dict_assign' else '__arr_get'}",
                        "pop de",
                        "pop de",
                        "push hl",
                    )
                expression(statement["value"])
                if statement["operator"] != "=":
                    emit("ex de,hl", "pop hl")
                    operator = statement["operator"]
                    if operator in {"*=", "//=", "%="}:
                        helper = {"*=": "mul", "//=": "div", "%=": "mod"}[operator]
                        if helper == "mod":
                            modulo_used = True
                        else:
                            math_used.add(helper)
                        emit(f"call __math_{helper}")
                    elif operator in {"&=", "|=", "^="}:
                        op = {"&=": "and", "|=": "or", "^=": "xor"}[operator]
                        emit(
                            "ld a,l", f"{op} e", "ld l,a", "ld a,h", f"{op} d", "ld h,a"
                        )
                    elif operator in {"<<=", ">>=", "**="}:
                        helper = {
                            "<<=": "shift_left",
                            ">>=": "shift_right",
                            "**=": "power",
                        }[operator]
                        emit(
                            "push hl",
                            "push de",
                            f"call __obj_{helper}",
                            "pop de",
                            "pop de",
                        )
                    else:
                        emit(
                            *(
                                ["add hl,de"]
                                if operator == "+="
                                else ["or a", "sbc hl,de"]
                            )
                        )
                emit(
                    "push hl",
                    f"call {'__dict_set' if kind == 'dict_assign' else '__arr_set'}",
                )
                for argument in (
                    statement["value"],
                    {**target["index"], "owned": managed(target["index"]["type"])},
                    {**target["base"], "owned": True},
                ):
                    emit("pop de")
                    if argument.get("owned"):
                        emit("push de", f"call {release(argument['type'])}", "pop de")
                continue
            if kind == "return":
                if statement["value"] is not None:
                    expression(statement["value"])
                    if managed(statement["value"]["type"]) and not statement[
                        "value"
                    ].get("owned"):
                        emit("push hl", "call __arr_retain", "pop de")
                if unwind:
                    if statement.get("completion_slot"):
                        store(statement["completion_slot"])
                    unwind_to(0)
                    if statement.get("completion_slot"):
                        load(statement["completion_slot"])
                emit(f"jp {return_label}")
                continue
            if kind == "while":
                start, end = label(), label()
                mark(start)
                branch_false(statement["condition"], end)
                loops.append((start, end, len(unwind)))
                statements(statement["body"])
                loops.pop()
                emit(f"jp {start}")
                mark(end)
                continue
            if kind == "for":
                if statement["iteration_kind"] == "iterator":
                    source = statement["sources"][0]
                    slot = statement["iterator_0"]
                    expression(source)
                    if not source.get("owned"):
                        emit("push hl", "call __arr_retain", "pop de")
                    emit("push hl")
                    load(slot)
                    emit("push hl", "call __arr_release", "pop de", "pop hl")
                    store(slot)
                    start, end = label(), label()
                    mark(start)
                    runtime(
                        ensure_iterator(slot["type"]), [{**slot, "kind": "name"}], ""
                    )
                    emit(
                        "ld de,(__iterator_exhausted)", "ld a,d", "or e", f"jp nz,{end}"
                    )
                    targets = statement["targets"]
                    if len(targets) > 1:
                        emit("push hl")
                        for i, target in enumerate(targets):
                            runtime(
                                "read",
                                [
                                    {
                                        "kind": "stack_dict",
                                        "type": iterable_element_type(source["type"]),
                                    },
                                    i,
                                ],
                                "__obj_",
                            )
                            store_borrowed(target)
                        emit("pop hl", "push hl", "call __arr_release", "pop de")
                    else:
                        target = targets[0]
                        if managed(target["type"]) and not target.get("cell"):
                            emit("push hl")
                            load(target)
                            emit(
                                "push hl",
                                f"call {release(target['type'])}",
                                "pop de",
                                "pop hl",
                            )
                        store(target)
                    loops.append((start, end, len(unwind)))
                    statements(statement["body"])
                    loops.pop()
                    emit(f"jp {start}")
                    mark(end)
                    load(slot)
                    emit("push hl", "call __arr_release", "pop de", "ld hl,0")
                    store(slot)
                    continue
                if statement["iteration_kind"] == "dict":
                    source = statement["sources"][0]
                    iterator, entry, version = (
                        statement["iterator_0"],
                        statement["iteration_index"],
                        statement["dict_version"],
                    )
                    expression(source)
                    if not source.get("owned"):
                        emit("push hl", "call __arr_retain", "pop de")
                    emit("push hl")
                    load(iterator)
                    emit("push hl", "call __dict_release", "pop de", "pop hl")
                    store(iterator)
                    for offset, slot in ((14, entry), (18, version)):
                        load(iterator)
                        emit(
                            f"ld de,{offset}",
                            "add hl,de",
                            "ld e,(hl)",
                            "inc hl",
                            "ld d,(hl)",
                            "ex de,hl",
                        )
                        store(slot)
                    start, advance, end = label(), label(), label()
                    mark(start)
                    load(entry)
                    emit("ld a,h", "or l", f"jp z,{end}")
                    offsets = (
                        (4, 6)
                        if statement["dict_view"] == "items"
                        else (6,) if statement["dict_view"] == "values" else (4,)
                    )
                    for offset, target in zip(offsets, statement["targets"]):
                        load(entry)
                        emit(
                            f"ld de,{offset}",
                            "add hl,de",
                            "ld e,(hl)",
                            "inc hl",
                            "ld d,(hl)",
                            "ex de,hl",
                        )
                        store_borrowed(target)
                    loops.append((advance, end, len(unwind)))
                    statements(statement["body"])
                    loops.pop()
                    mark(advance)
                    load(iterator)
                    emit(
                        "ld de,18",
                        "add hl,de",
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "push de",
                    )
                    load(version)
                    emit("pop de", "or a", "sbc hl,de")
                    unchanged = label()
                    emit(f"jp z,{unchanged}", "ld hl,13", "jp __arr_fail")
                    mark(unchanged)
                    load(entry)
                    emit(
                        "ld de,8",
                        "add hl,de",
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "ex de,hl",
                    )
                    store(entry)
                    emit(f"jp {start}")
                    mark(end)
                    load(iterator)
                    emit("push hl", "call __dict_release", "pop de", "ld hl,0")
                    store(iterator)
                    continue
                if statement["iteration_kind"] == "tuple":
                    rows = statement["iterable"]["values"]
                    pointer, index = (
                        statement["iterator_0"],
                        statement["iteration_index"],
                    )
                    targets = statement["targets"]
                    arity = len(targets)
                    static = "constant_rows" in statement
                    if static:
                        values = [
                            int(value) & 65535
                            for row in statement["constant_rows"]
                            for value in row
                        ]
                        data = tuple(
                            b for value in values for b in (value & 255, value >> 8)
                        )
                        if data not in constant_data:
                            constant_data[data] = (
                                f"__constant_data_{len(constant_data)}"
                            )
                        emit(f"ld hl,{constant_data[data]}")
                    else:
                        # Snapshot all components once, in Python's row-major
                        # evaluation order, before executing the loop body.
                        for row in rows:
                            for value in row["values"]:
                                expression(value)
                                emit("push hl")
                        emit(f"ld hl,{(len(rows) * arity - 1) * 2}", "add hl,sp")
                    store(pointer)
                    emit("ld hl,0")
                    store(index)
                    start, advance, end = label(), label(), label()
                    mark(start)
                    load(index)
                    emit(f"ld de,{len(rows)}", "or a", "sbc hl,de", f"jp nc,{end}")
                    for i, target in enumerate(targets):
                        load(pointer)
                        if i:
                            emit(
                                f"ld de,{(i * (2 if static else -2)) & 65535}",
                                "add hl,de",
                            )
                        emit("ld e,(hl)", "inc hl", "ld d,(hl)", "ex de,hl")
                        store(target)
                    loops.append((advance, end, len(unwind)))
                    statements(statement["body"])
                    loops.pop()
                    mark(advance)
                    load(pointer)
                    emit(
                        f"ld de,{(arity * (2 if static else -2)) & 65535}", "add hl,de"
                    )
                    store(pointer)
                    load(index)
                    emit("inc hl")
                    store(index)
                    emit(f"jp {start}")
                    mark(end)
                    if not static:
                        emit(f"ld hl,{len(rows) * arity * 2}", "add hl,sp", "ld sp,hl")
                    continue
                if "fast_range" in statement:
                    info, current = statement["fast_range"], statement["range_current"]
                    start, advance, end = label(), label(), label()
                    emit(f"ld hl,{info['start'] & 65535}")
                    store(current)
                    mark(start)
                    load(current)
                    store(statement["target"])
                    loops.append((advance, end, len(unwind)))
                    statements(statement["body"])
                    loops.pop()
                    mark(advance)
                    load(current)
                    if info["byte_compare"]:
                        emit("ld a,l", f"cp {info['last']}", f"jp z,{end}")
                    else:
                        emit(
                            "push hl",
                            f"ld de,{info['last'] & 65535}",
                            "or a",
                            "sbc hl,de",
                            "pop hl",
                            f"jp z,{end}",
                        )
                    step = info["step"]
                    if abs(step) <= 3:
                        emit(*(["inc hl" if step > 0 else "dec hl"] * abs(step)))
                    else:
                        emit(f"ld de,{step & 65535}", "add hl,de")
                    store(current)
                    emit(f"jp {start}")
                    mark(end)
                    continue
                if statement["iteration_kind"] == "range":
                    current, stop_slot, step = [
                        statement[field]
                        for field in ("range_current", "range_stop", "range_step")
                    ]
                    arguments = statement["iterable"]["arguments"]
                    arguments = (
                        [0, arguments[0], 1]
                        if len(arguments) == 1
                        else [*arguments, 1] if len(arguments) == 2 else arguments
                    )
                    for argument, slot in zip(arguments, [current, stop_slot, step]):
                        if isinstance(argument, int):
                            emit(f"ld hl,{argument}")
                        else:
                            expression(argument)
                        store(slot)
                    start, negative, body, advance, negative_advance, update, end = [
                        label() for _ in range(7)
                    ]
                    load(step)
                    emit("ld a,h", "or l", f"jp nz,{start}", "ld hl,7", "jp __arr_fail")
                    mark(start)
                    load(step)
                    emit("bit 7,h", f"jp nz,{negative}")

                    def range_condition(operator):
                        branch_false(
                            {
                                "kind": "compare",
                                "operator": operator,
                                "left": {**current, "kind": "name"},
                                "right": {**stop_slot, "kind": "name"},
                            },
                            end,
                        )

                    range_condition("<")
                    emit(f"jp {body}")
                    mark(negative)
                    range_condition(">")
                    mark(body)
                    load(current)
                    store(statement["target"])
                    loops.append((advance, end, len(unwind)))
                    statements(statement["body"])
                    loops.pop()
                    mark(advance)
                    load(step)
                    emit("bit 7,h", f"jp nz,{negative_advance}", "push hl")
                    load(stop_slot)
                    emit("push hl")
                    load(current)
                    emit(
                        "ex de,hl",
                        "pop hl",
                        "or a",
                        "sbc hl,de",
                        "pop de",
                        "or a",
                        "sbc hl,de",
                        f"jp c,{end}",
                        f"jp z,{end}",
                        f"jp {update}",
                    )
                    mark(negative_advance)
                    load(step)
                    emit(
                        "ld a,l",
                        "cpl",
                        "ld l,a",
                        "ld a,h",
                        "cpl",
                        "ld h,a",
                        "inc hl",
                        "push hl",
                    )
                    load(current)
                    emit("push hl")
                    load(stop_slot)
                    emit(
                        "ex de,hl",
                        "pop hl",
                        "or a",
                        "sbc hl,de",
                        "pop de",
                        "or a",
                        "sbc hl,de",
                        f"jp c,{end}",
                        f"jp z,{end}",
                    )
                    mark(update)
                    load(current)
                    emit("push hl")
                    load(step)
                    emit("ex de,hl", "pop hl", "add hl,de")
                    store(current)
                    emit(f"jp {start}")
                    mark(end)
                    continue
                iterators = statement["iterators"]
                index = statement["iteration_index"]
                start, advance, end = label(), label(), label()
                for source, iterator in zip(statement["sources"], iterators):
                    expression(source)
                    if not source.get("owned"):
                        emit("push hl", "call __arr_retain", "pop de")
                    emit("push hl")
                    load(iterator)
                    emit("push hl", "call __arr_release", "pop de", "pop hl")
                    store(iterator)
                if statement["iteration_kind"] == "enumerate":
                    expression(statement["enumeration_start"])
                    store(statement["enumeration"])
                emit("ld hl,0")
                store(index)
                mark(start)
                for iterator in iterators:
                    load(iterator)
                    emit("inc hl", "inc hl", "ld e,(hl)", "inc hl", "ld d,(hl)")
                    load(index)
                    emit("or a", "sbc hl,de", f"jp nc,{end}")
                # The loop just checked index < current length. No user code
                # runs between that check and this read, so normalization and
                # a second bounds check inside __arr_get would be redundant.
                if statement["iteration_kind"] == "enumerate":
                    load(statement["enumeration"])
                    store(statement["targets"][0])
                for iterator, target in zip(
                    iterators, statement["targets"][-len(iterators) :]
                ):
                    load(index)
                    if array_width(iterator["type"]) != 1:
                        emit("add hl,hl")
                    emit("push hl")
                    load(iterator)
                    emit(
                        "ld e,(hl)",
                        "inc hl",
                        "ld d,(hl)",
                        "ex de,hl",
                        "pop de",
                        "add hl,de",
                    )
                    if array_width(iterator["type"]) != 1:
                        emit("ld e,(hl)", "inc hl", "ld d,(hl)", "ex de,hl")
                    else:
                        emit("ld l,(hl)", "ld h,0")
                    if statement["iteration_kind"] == "unpack_array":
                        emit("push hl")
                        for field_index, field in enumerate(statement["targets"]):
                            emit(
                                "pop hl",
                                "push hl",
                                "ld e,(hl)",
                                "inc hl",
                                "ld d,(hl)",
                                "ex de,hl",
                                f"ld de,{field_index*2}",
                                "add hl,de",
                                "ld e,(hl)",
                                "inc hl",
                                "ld d,(hl)",
                                "ex de,hl",
                            )
                            store_borrowed(field)
                        emit("pop de")
                    else:
                        store_borrowed(target)
                loops.append((advance, end, len(unwind)))
                statements(statement["body"])
                loops.pop()
                mark(advance)
                load(index)
                emit("inc hl")
                store(index)
                if statement["iteration_kind"] == "enumerate":
                    load(statement["enumeration"])
                    emit("inc hl")
                    store(statement["enumeration"])
                emit(f"jp {start}")
                mark(end)
                for iterator in iterators:
                    load(iterator)
                    emit("push hl", "call __arr_release", "pop de", "ld hl,0")
                    store(iterator)
                continue
            if kind in {"break", "continue"}:
                start, end, depth = loops[-1]
                unwind_to(depth)
                emit(f"jp {end if kind == 'break' else start}")
                continue
            if kind == "if":
                false, end = label(), label()
                branch_false(statement["condition"], false)
                statements(statement["body"])
                emit(f"jp {end}")
                mark(false)
                statements(statement["alternative"])
                mark(end)
                continue
            expression(statement["value"])
            if managed(statement["type"]):
                if not statement["value"].get("owned"):
                    emit("push hl", "call __arr_retain", "pop de")
                if not statement.get("cell"):
                    emit("push hl")
                    load(statement)
                    emit(
                        "push hl",
                        f"call {release(statement['type'])}",
                        "pop de",
                        "pop hl",
                    )
            store(statement)

    if errors_used:
        emit("ld (__list_main_sp),sp", "ld (__list_main_ix),ix")
    if lists_used:
        emit("call __arr_init")
    if native_used & {"random.seed", "random.randint"}:
        emit("ld a,r", "ld l,a", "ld h,173", "ld (__native_rng_state),hl")
    statements(tree["body"])
    # Keep the CPU from executing the variable slots as instructions.
    emit("ret")
    for function in tree["body"]:
        if function["kind"] != "function":
            continue
        code.extend(["", f"; Function {function['name']}: per-call IX stack frame"])
        mark(function["label"])
        if "finite_table" in function:
            table = function["finite_table"]
            data = function["label"] + "_table"
            emit(
                "ld hl,2",
                "add hl,sp",
                "ld a,(hl)",
                f"and {table['mask']}",
                "ld l,a",
                "ld h,0",
                "add hl,hl",
                f"ld de,{data}",
                "add hl,de",
                "ld e,(hl)",
                "inc hl",
                "ld d,(hl)",
                "ex de,hl",
                "ret",
            )
            mark(data)
            code.append(
                "        dw "
                + ",".join(str(value & 65535) for value in table["values"])
            )
            continue
        emit("push ix", "ld ix,0", "add ix,sp")
        if bounded_stack:
            if function["frame_size"]:
                emit(f"ld hl,{(-function['frame_size']) & 0xFFFF}","add hl,sp","ld sp,hl")
        else:
            emit(f"ld hl,{(-function['frame_size']) & 0xFFFF}", "add hl,sp", "push hl")
            if generators:
                emit("call __obj_stack_floor", "ex de,hl", "pop hl", "push hl")
            else:
                emit("ld de,__program_end+256")
            emit("or a", "sbc hl,de", "pop hl")
            room = label()
            emit(f"jp nc,{room}", "ld hl,3", "jp __arr_fail")
            mark(room)
            emit("ld sp,hl")
        return_label = label()
        array_locals = [
            value for value in function["locals"].values() if managed(value["type"])
        ]
        if array_locals:
            emit("ld hl,0")
            for value in array_locals:
                if value["offset"] < 0 and not value.get("cell"):
                    store({**value, "symbol": value["label"]})
        for value in function["locals"].values():
            if not value.get("cell"):
                continue
            raw = {k: v for k, v in value.items() if k != "cell"}
            raw["symbol"] = value["label"]
            if value["offset"] > 0:
                load(raw)
            else:
                emit("ld hl,0")
            emit("push hl")
            flags = (int(managed(value["type"])), 0)
            if flags not in constant_data:
                constant_data[flags] = f"__constant_data_{len(constant_data)}"
            runtime(
                "tuple_new", [{"kind": "address", "label": constant_data[flags]}, 2]
            )
            store(raw)
            # cell_set takes the stored cell and the original parameter value.
            if value["offset"] > 0:
                emit(
                    "pop de",
                    "push hl",
                    "push de",
                    "call __obj_cell_set",
                    "pop de",
                    "pop de",
                )
            else:
                emit("pop de")
        statements(function["body"])
        if function["return_type"] == "void":
            emit("ld hl,0")
        mark(return_label)
        cleanup = [
            v
            for v in function["locals"].values()
            if managed(v["type"]) or v.get("cell")
        ]
        if cleanup:
            emit("push hl")
            for value in cleanup:
                load(
                    {
                        **{k: v for k, v in value.items() if k != "cell"},
                        "symbol": value["label"],
                    }
                )
                emit(
                    "push hl",
                    f"call {'__arr_release' if value.get('cell') else release(value['type'])}",
                    "pop de",
                )
            emit("pop hl")
        emit("ld sp,ix", "pop ix", "ret")
    if mesh_programs:
        code.extend(source for _, source in mesh_programs.values())
        code.append(
            "__gfx_mesh_programs: dw "
            + ",".join(
                value
                for model, (name, _) in mesh_programs.items()
                for value in (model, name)
            )
            + ",0"
        )
    if text_used:
        from .text_runtime import assembly as text_assembly

        glyphs = set()
        for node in text_nodes:
            value = node["arguments"][0]
            if (
                node["kind"] == "text_input"
                or value["type"] == "str"
                and value.get("kind") != "string"
            ):
                glyphs = None
                break
            if value.get("kind") == "string":
                glyphs.update(value["value"])
            elif value["type"] == "bool":
                glyphs.update("FalseTrue")
            elif value["type"] in {"i16", "u8"}:
                glyphs.update("-0123456789")
            else:
                glyphs = None
                break
        code.append(text_assembly(text_used, glyphs))
    if any(n.get("kind") == "key_down" for n in walk(tree)):
        from .keyboard import assembly as keyboard_assembly

        code.append(keyboard_assembly())
    if native_used:
        from .native_runtime import assembly as native_assembly

        code.append(native_assembly(native_used))
    if modulo_used:
        from .math_runtime import MODULO

        code.append(MODULO)
    if math_used:
        from .math_runtime import MULTIPLY, FLOOR_DIVIDE

        code.extend(
            {"mul": MULTIPLY, "div": FLOOR_DIVIDE}[name] for name in sorted(math_used)
        )
    if iterator_new_used:
        from .iterator_runtime import new_routine

        iterator_code.append(new_routine())
    code.extend(iterator_code)
    if tracing:
        fatal = "        ld (__list_error),hl\n        ld sp,(__list_main_sp)\n        ld ix,(__list_main_ix)\n        ret"
        code[:] = [part.replace(fatal, "        jp __arr_fail") for part in code]
    if pixels_used:
        from .graphics_runtime import graphics_assembly

        mesh_calls = [
            n
            for n in walk(tree)
            if n.get("kind") == "native_graphics" and n.get("name") == "mesh_draw"
        ]
        packets = [
            n
            for n in walk(tree)
            if n.get("kind") == "geometry_data" and not n.get("collision_map")
        ]
        shadow_calls = [
            node
            for node in walk(tree)
            if node.get("kind") == "native_graphics"
            and node.get("name") == "mesh_shadow"
        ]
        if shadow_calls and not mesh_calls:
            pixels_used.update({"solid_no_depth", "solid_no_cull", "solid_only"})

        def flag(n, i, v):
            a = n["arguments"][i]
            if a.get("kind") == "name":
                a = next(
                    (
                        e["value"]
                        for e in n.get("evaluations", [])
                        if e["slot"]["label"] == a.get("label")
                    ),
                    a,
                )
            return a.get("kind") == "boolean" and a["value"] == v

        def fixed_argument(node, index):
            argument = node["arguments"][index]
            if argument.get("kind") == "name":
                argument = next(
                    (
                        evaluation["value"]
                        for evaluation in node.get("evaluations", [])
                        if evaluation["slot"]["label"] == argument.get("label")
                    ),
                    argument,
                )
            if argument.get("kind") == "number":
                return argument["value"]
            if (
                argument.get("kind") == "negate"
                and argument["value"].get("kind") == "number"
            ):
                return -argument["value"]["value"]
            return None

        for bits in range(1, 5):
            scaled_calls = [
                node
                for node in walk(tree)
                if node.get("kind") == "native_graphics"
                and node.get("name") == f"mesh_look_q{bits}"
            ]
            for index in range(6):
                if scaled_calls and all(
                    fixed_argument(node, index) is not None
                    and -120 * (1 << bits)
                    <= fixed_argument(node, index)
                    <= 120 * (1 << bits)
                    for node in scaled_calls
                ):
                    pixels_used.add(f"q{bits}_camera_checked_{index}")

        camera_setups = [
            node
            for node in walk(tree)
            if node.get("kind") == "native_graphics"
            and node.get("name") in {"mesh_init", "mesh_fov"}
        ]
        if (
            not tree.get("mesh_frustum_disabled")
            and camera_setups
            and all(
                fixed_argument(node, 0) is not None
                and 55 <= fixed_argument(node, 0) <= 75
                for node in camera_setups
            )
        ):
            pixels_used.add("mesh_tight_frustum")

        shadow_coordinates = ("light_x", "light_y", "light_z", "floor_y", "x", "y", "z")
        for index, name in enumerate(shadow_coordinates, start=1):
            if shadow_calls and all(
                fixed_argument(node, index) is not None
                and -120 <= fixed_argument(node, index) <= 120
                for node in shadow_calls
            ):
                pixels_used.add("shadow_checked_" + name)
        if shadow_calls and all(
            fixed_argument(node, 2) is not None
            and fixed_argument(node, 4) is not None
            and fixed_argument(node, 2) > fixed_argument(node, 4)
            for node in shadow_calls
        ):
            pixels_used.add("shadow_above_floor")

        if shadow_calls:
            scales = {
                fixed_argument(node, 2) - fixed_argument(node, 4)
                for node in shadow_calls
                if fixed_argument(node, 2) is not None
                and fixed_argument(node, 4) is not None
            }
            if len(scales) == 1 and all(
                fixed_argument(node, 2) is not None
                and fixed_argument(node, 4) is not None
                for node in shadow_calls
            ):
                pixels_used.add("shadow_scale_" + str(scales.pop()))
            if all(
                fixed_argument(node, 6) is not None
                and fixed_argument(node, 6) == fixed_argument(node, 4)
                for node in shadow_calls
            ):
                pixels_used.add("shadow_grounded")

        if mesh_calls and all(
            all(fixed_argument(node, index) == 0 for index in (4, 5, 6))
            and fixed_argument(node, 7) == 256
            for node in mesh_calls
        ):
            pixels_used.add("mesh_unit_transform")

        if (
            mesh_calls
            and packets
            and all(p.get("face_count", 0) > 0 for p in packets)
            and all(flag(n, 8, True) and flag(n, 12, False) for n in mesh_calls)
        ):
            pixels_used.add("solid_only")
        if mesh_calls and all(
            node.get("legacy_lighting") is False or flag(node, 10, False)
            for node in mesh_calls
        ):
            pixels_used.add("solid_no_legacy_light")
        if mesh_calls and not all(flag(n, 12, False) for n in mesh_calls):
            pixels_used.add("mesh_outlines")
        if mesh_calls and all(flag(n, 9, False) for n in mesh_calls):
            pixels_used.add("solid_no_cull")
        if mesh_calls and all(flag(n, 11, False) for n in mesh_calls):
            pixels_used.add("solid_no_depth")
        elif mesh_calls and not all(flag(n, 11, True) for n in mesh_calls):
            pixels_used.add("solid_dynamic_depth")
        if tree.get("mesh_basic_spans"):
            pixels_used.add("mesh_basic_spans")
        from .render_quality import apply as apply_render_quality

        code.append(
            apply_render_quality(
                graphics_assembly(pixels_used), tree.get("render_quality", "exact")
            )
        )
    required = set(RUNTIME_TARGET.findall("\n".join(code)))
    if required:
        object_types = {
            t
            for node in walk(tree)
            if isinstance(node.get("type"), str)
            for t in all_types(node["type"])
        }
        if pixels_used & {"mat_push", "mat_pop", "cam_fill", "cam_vertex"}:
            object_types.update(
                {"i16[][]", "i16[]", "tuple[i16,i16][]", "tuple[i16,i16]"}
            )
        if any(
            n.get("kind") == "graphics" and n.get("name") == "triangle"
            for n in walk(tree)
        ):
            object_types.update({"tuple[i16,i16][]", "tuple[i16,i16]"})
        dictionary_types = {
            dict_types(t) if is_dict(t) else (element_type(t), "i16")
            for t in object_types
            if is_dict(t) or is_set(t)
        }
        code.extend(
            [
                "",
                "; Used heap runtime routines and their dependencies",
                runtime_assembly(
                    required,
                    dictionary_types,
                    object_types,
                    tracing,
                    generators,
                    _heap_bytes,
                ),
            ]
        )
    for name, program in vertex_programs.values():
        code.append(program)
    for label_name, program, values in geometry_packets.values():
        code.append(label_name + ":\n        dw " + program)
        for start in range(0, len(values), 24):
            code.append("        db " + ",".join(map(str, values[start : start + 24])))
    for values, label_name in constant_data.items():
        code.append(label_name + ":")
        for start in range(0, len(values), 24):
            code.append(
                "        db "
                + ",".join(str(value) for value in values[start : start + 24])
            )
    code.extend(["", "; Variable storage"])
    for name, symbol in symbols.items():
        directive = "db" if symbol["size"] == 1 else "dw"
        code.append(f"{symbol['label']}: {directive} 0 ; {name}: {symbol['type']}")
    for symbol in tree.get("temporaries", {}).values():
        code.append(f"{symbol['label']}: dw 0")
    for symbol in tree.get("drawing_buffers", []):
        code.append(f"{symbol['label']}: defs {symbol['size']},0")
    if errors_used:
        code.extend(
            ["__list_error: dw 0", "__list_main_sp: dw 0", "__list_main_ix: dw 0"]
        )
    if tracing:
        code.extend(
            [
                "__exception_handler: dw 0",
                "__exception_code: dw 0",
                "__exception_message: dw 0",
                "__gc_changed: dw 0",
                "__gc_roots:",
            ]
        )
        roots = [
            s["label"]
            for s in [*symbols.values(), *tree.get("temporaries", {}).values()]
            if managed(s["type"])
        ]
        if pixels_used & {"mat_push", "mat_pop"}:
            roots.append("__gfx_model_stack")
        code.append(
            "        dw "
            + ",".join(
                [
                    *roots,
                    "__exception_message",
                    *(["__gen_active", "__gen_value"] if generators else []),
                    "0",
                ]
            )
        )
    if generators:
        code.extend(
            [
                "__gen_active: dw 0",
                "__gen_value: dw 0",
                "__gen_continuation: dw 0",
                "__gen_finished_object: dw 0",
            ]
        )
    if iterator_helpers or iterator_new_used:
        code.append("__iterator_exhausted: dw 0")
    if lists_used:
        code.extend(["__heap_start:", f"        defs {_heap_bytes},0", "__heap_end:"])
    code.append("__program_end:")
    assembly = optimize("\n".join(code) + "\n")
    if tree.get("game_runtime"):
        # Fatal errors unwind directly to the main caller. Release the game
        # timer and restore its original CPU mode on that path as well.
        cleanup = "__gfx_mesh_session_end" if tree.get("graphics_session") else "__gfx_mesh_game_end"
        release_clock = "" if tree.get("graphics_session") else "        call __native_stop_frame_timer\n"
        assembly = assembly.replace(
            "        ld sp,(__list_main_sp)\n        ld ix,(__list_main_ix)\n        ret",
            f"{release_clock}        call {cleanup}\n        ld sp,(__list_main_sp)\n        ld ix,(__list_main_ix)\n        ret",
        )
    if "__gfx_solid_fast_span_tables:" in assembly:
        from .assembly_size import estimate

        lines = []
        offset = 0x9D95
        for line in assembly.splitlines():
            if line.startswith("__gfx_solid_fast_span_tables:"):
                padding = (-offset) & 255
                if padding:
                    lines.append(f"        defs {padding},0")
                    offset += padding
            lines.append(line)
            offset += estimate(line)
        assembly = "\n".join(lines) + "\n"

    if (
        not lists_used
        and "mesh_faces" in pixels_used
        and pixels_used & {"mesh_draw", "mesh_shadow"}
    ):
        from .assembly_size import estimate
        from .errors import CompileError

        if estimate(assembly) > 0xFFFE - 0x9D95 - 1024:
            # Speed specializations must not turn a fitting program into a
            # compile error. Retry compact kernels before enforcing RAM limits.
            if not tree.get("mesh_basic_spans"):
                return generate(
                    {**original_tree, "mesh_basic_spans": True},
                    symbols,
                    optimize_graphics=optimize_graphics,
                    _heap_bytes=_heap_bytes,
                )
            if compiled_mesh_vertices:
                return generate(
                    {
                        **original_tree,
                        "mesh_vertices_disabled": True,
                        "mesh_basic_spans": True,
                    },
                    symbols,
                    optimize_graphics=optimize_graphics,
                    _heap_bytes=_heap_bytes,
                )
            location = next(
                (n for n in tree["body"] if "line" in n), {"line": 1, "column": 1}
            )
            raise CompileError(
                "Solid 3D program exceeds TI-84 Plus RAM with 1 KB reserved for the stack; simplify geometry or code",
                location,
            )
    if lists_used:
        from .assembly_size import estimate
        from .errors import CompileError

        code_bytes = estimate(assembly) - _heap_bytes
        available = (0xFFFE - 0x9D95 - code_bytes - 1024) // 256 * 256
        chosen = min(HEAP_BYTES, available)
        if chosen < 512:
            location = next(
                (n for n in tree["body"] if "line" in n), {"line": 1, "column": 1}
            )
            raise CompileError(
                "Program code leaves insufficient RAM for the heap and stack", location
            )
        if chosen != _heap_bytes:
            return generate(
                original_tree,
                symbols,
                optimize_graphics=optimize_graphics,
                _heap_bytes=chosen,
            )
    return assembly
