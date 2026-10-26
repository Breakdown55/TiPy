"""Shared static storage categories."""


def is_dict(type_name):
    return type_name.startswith("dict[") and type_name.endswith("]") and type_name.count("[") == 1 and type_name.count("]") == 1


def dict_types(type_name):
    return tuple(type_name[5:-1].split(","))


def managed(type_name):
    return type_name == "str" or type_name.endswith("[]") or is_dict(type_name)


def release(type_name):
    return "__dict_release" if is_dict(type_name) else "__arr_release"
