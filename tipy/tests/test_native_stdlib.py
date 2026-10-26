from pathlib import Path
import unittest

from tipy.compiler import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError


class NativeStdlibTests(unittest.TestCase):
    def test_fireworks_project_compiles_with_nested_import_and_no_heap(self):
        source = Path("examples/fireworks.tip").read_text()
        result = compile_project({"main.tip": source}, "main.tip")
        assembly = result["assembly"]
        self.assertIn("__native_randint:", assembly)
        self.assertIn("__native_sleep:", assembly)
        self.assertIn("__native_sin_table:", assembly)
        self.assertNotIn("__heap_start", assembly)
        self.assertNotIn("Function main.draw_circle:", assembly)
        self.assertFalse(any(p.get("applied") for p in result["optimization_report"] if p["pass"] == "whole-program-specialization"))

    def test_unused_standard_imports_link_nothing(self):
        result = compile_source("import time\nimport random\nimport math\npass")
        self.assertNotIn("__native_", result["assembly"])
        self.assertNotIn("__math_", result["assembly"])

    def test_native_bindings_aliases_and_selective_imports(self):
        result = compile_project({"main.tip": "import math as m\nfrom random import randint as roll\nx=roll(0,2)\ny=int(m.sin(m.radians(x)))"}, "main.tip", optimization="reference")
        self.assertEqual(result["symbols"]["x"]["type"], "i16")
        self.assertIn("call __native_randint", result["assembly"])

    def test_inference_keeps_static_types_and_definite_initialization(self):
        result = compile_source("x=[1,2]\ny=True\ndef f(a,b):\n    total=a+b\n    return total\nz=f(1,2)", optimization="reference")
        self.assertEqual(result["symbols"]["x"]["type"], "i16[]")
        self.assertEqual(result["symbols"]["y"]["type"], "bool")
        for source in ["x=1\nx=True", "def f(a):\n    if a>0:\n        b=1\n    return b\nx=f(0)", "x=x+1"]:
            with self.subTest(source=source), self.assertRaises(CompileError): compile_source(source)

    def test_implicit_locals_shadow_globals(self):
        result = compile_source("A=1\ndef f():\n    A=2\nf()", optimization="reference")
        function = next(n for n in result["checked"]["body"] if n["kind"] == "function")
        self.assertEqual(function["locals"]["A"]["storage"], "frame")
        self.assertEqual(result["symbols"]["A"]["type"], "i16")

    def test_clear_override_and_native_clear_are_distinct(self):
        result = compile_project({"main.tip": "def clear():\n    pixel_on(1,2)\nclear()\nrender()"}, "main.tip", optimization="reference")
        self.assertIn("call __fn_0", result["assembly"])
        self.assertNotIn("__gfx_clear:", result["assembly"])

    def test_invalid_native_calls_have_source_errors(self):
        cases = [("import math\nx=math.sqrt(4)", "Unsupported math member"),
                 ("import random\nx=random.randint(True,5)", "integer arguments"),
                 ("import time\ntime.sleep(-0.1)", "sleep must"),
                 ("import time\ntime.sleep(33)", "sleep must"),
                 ("i32 x=2147483648", "outside i32"),
                 ("import math\nx=math.sin(1.0)\nx=3", "convert fixed-point"),
                 ("import time\ntime=1", "rebind")]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message): compile_source(source)

    def test_sleep_literal_preserves_milliseconds_and_does_not_render(self):
        result = compile_source("import time\ntime.sleep(0.04)", optimization="reference")
        self.assertIn("ld hl,40", result["assembly"])
        self.assertNotIn("__gfx_render", result["assembly"])
