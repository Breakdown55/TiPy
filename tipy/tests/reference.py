"""Structural/ABI tests deliberately exercise the reference compiler."""

from tipy import compile_source as _compile_source
from tipy.project import compile_project as _compile_project


def compile_source(source, **kwargs):
    return _compile_source(source, optimization="reference", **kwargs)


def compile_project(files, entry):
    return _compile_project(files, entry, optimization="reference")
