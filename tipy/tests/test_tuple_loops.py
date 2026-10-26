import unittest
from pathlib import Path

from tipy.compiler import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError


class TupleLoopTests(unittest.TestCase):
    def test_literal_tuple_loop_uses_constant_data_without_heap(self):
        result = compile_source("for x,y in [(1,2),(3,4)]:\n    pixel_on(x,y)\nrender()", optimization="reference")
        self.assertNotIn("__heap_start", result["assembly"])
        self.assertIn("__constant_data_0:", result["assembly"])

    def test_tuple_shape_type_and_initialization_errors_are_source_errors(self):
        cases = [("for x,y in [(1,2),(3,)]:\n    pass", "2 values"),
                 ("for x,y in [(1,2),3]:\n    pass", "Cannot assign"),
                 ("for x,y in [(1,True),(2,3)]:\n    pass", "Cannot assign"),
                 ("for x,y,z in [([1],2)]:\n    pass", "2 target"),
                 ("for x,y,z in [(1,2)]:\n    pass", "3 values"),
                 ("for x,y in []:\n    pass\nz=x", "may not be initialized"),
                 ("x=(1,2)\na,b,c=x", "Wrong number")]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message): compile_source(source)

    def test_nested_comprehension_targets_remain_private(self):
        result = compile_source("x=99\nvalues=[x+y for x,y in [(1,2),(3,4)]]", optimization="reference")
        self.assertEqual(set(result["symbols"]), {"x", "values"})

    def test_digital_rain_project_compiles(self):
        source = Path("examples/digital_rain.tip").read_text()
        result = compile_project({"main.tip": source}, "main.tip")
        self.assertIn("__native_randint:", result["assembly"])
        self.assertIn("__native_sleep:", result["assembly"])
        self.assertIn("__constant_data_", result["assembly"])
        self.assertNotIn("__native_sin", result["assembly"])
