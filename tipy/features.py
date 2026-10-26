"""Runtime capabilities required by reachable checked code."""
from .types import all_types,is_object


def walk(value):
    if isinstance(value,dict):
        if value.get("unreachable") is True:return
        yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)


def runtime_features(tree):
    nodes=[n for n in walk(tree.get("body",[])) if not n.get("unreachable")]
    generators=any(isinstance(n.get("kind"),str) and n["kind"] in {"function","call","closure_call"} and n.get("generator") is True for n in nodes)
    tracing=generators or any(isinstance(n.get("kind"),str) and (n["kind"] in {"try_except","try_finally","raise","assert"} or n.get("cycle_capture") is True or n["kind"]=="object_assign" and any(is_object(t) for t in all_types(n.get("type","")))) for n in nodes)
    return tracing,generators
