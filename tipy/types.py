"""Shared static storage categories."""


def is_dict(type_name):
    return type_name.startswith("dict[") and type_name.endswith("]") and not type_name.endswith("[]")


def dict_types(type_name):
    return tuple(split_types(type_name[5:-1]))


def split_types(text):
    depth, start, result = 0, 0, []
    for i, char in enumerate(text):
        if char == "[": depth += 1
        elif char == "]": depth -= 1
        elif char == "," and depth == 0:
            result.append(text[start:i]); start = i + 1
    if text[start:]: result.append(text[start:])
    return result


def is_tuple(t): return t=="tuple[]" or t.startswith(("tuple[","tuple_var[","tuple_cycle[")) and t.endswith("]") and not t.endswith("[]")
def tuple_types(t): return split_types(t[t.index("[")+1:-1])
def dynamic_tuple(t):return t.startswith(("tuple_var[","tuple_cycle["))
def is_cell(t):return t.startswith("cell[") and t.endswith("]") and not t.endswith("[]")
def is_closure(t):return t.startswith("closure[") and t.endswith("]") and not t.endswith("[]")
def is_iterator(t):return t.startswith("iter[") and t.endswith("]") and not t.endswith("[]")
def is_optional(t):return t.startswith("optional[") and t.endswith("]") and not t.endswith("[]")
def iterator_types(t):return split_types(t[5:-1])
def is_set(t): return t.startswith("set[") and t.endswith("]") and not t.endswith("[]")
def is_view(t):return t.startswith("view[") and t.endswith("]") and not t.endswith("[]")
def view_types(t):return split_types(t[5:-1])
def iterable_element(t):
    if is_iterator(t):return iterator_types(t)[0]
    if t=="str":return "str"
    if is_view(t):
        mode,source=view_types(t);key,value=dict_types(source)
        return f"tuple[{key},{value}]" if mode=="items" else value if mode=="values" else key
    if is_dict(t):return dict_types(t)[0]
    if is_set(t):return element(t)
    if is_tuple(t):
        fields=tuple_types(t)
        return fields[0] if fields and len(set(fields))==1 else "i16" if not fields else None
    if t.endswith("[]"):return t[:-2]
    return None
def element(t): return t[:-2] if t.endswith("[]") else t[4:-1] if is_set(t) else "i16"
def width(t): return 32765 if managed(element(t)) else 1 if element(t) in {"u8", "bool"} else 2
def complex_type(t): return is_optional(t) or is_iterator(t) or is_cell(t) or is_closure(t) or is_tuple(t) or is_set(t) or is_view(t) or t.endswith("[]") and managed(t[:-2]) or is_dict(t) and managed(dict_types(t)[1]) and dict_types(t)[1] != "str"


def all_types(t):
    yield t
    children = [t[9:-1]] if is_optional(t) else iterator_types(t)[2:] if is_iterator(t) else dict_types(t) if is_dict(t) else tuple_types(t) if is_tuple(t) else [view_types(t)[1]] if is_view(t) else [t[5:-1]] if is_cell(t) else [element(t)] if is_set(t) or t.endswith("[]") else []
    for child in children: yield from all_types(child)


def managed(type_name):
    return type_name in {"str","exception"} or type_name.endswith("[]") or is_dict(type_name) or is_tuple(type_name) or is_set(type_name) or is_view(type_name) or is_cell(type_name) or is_closure(type_name) or is_iterator(type_name) or is_optional(type_name)


def release(type_name):
    return "__dict_release" if is_dict(type_name) or is_set(type_name) else "__arr_release"
