import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class ForTests(unittest.TestCase):
    def test_array_parameter_and_inferred_loop_element_type(self):
        result = compile_source("def get_sum(int[] x) -> int:\n    int total=0\n    for num in x:\n        total+=num\n    return total\nint x=[1,2,3,4]\nint total=get_sum(x)")
        function = result["checked"]["body"][0]
        self.assertEqual(function["params"][0]["type"], "i16[]")
        self.assertEqual(function["body"][1]["target"]["type"], "i16")
        self.assertNotIn("call __arr_get", result["assembly"])
        self.assertIn("call __arr_retain", result["assembly"])

    def test_byte_and_bool_array_literals_are_context_typed_arguments(self):
        compile_source("def f(u8[] x) -> int:\n    int count=0\n    for value in x:\n        count+=value\n    return count\nint result=f([1,255])")
        compile_source("def f(bool[] x) -> bool:\n    for flag in x:\n        if flag:\n            return True\n    return False\nbool result=f([False,True])")

    def test_rejects_invalid_iteration_and_parameter_types(self):
        cases = [
            ("for x in 1:\n    pass", "requires an array"),
            ("def f(int[] x) -> int:\n    return len(x)\nint n=f(1)", "Cannot assign"),
            ("def f(u8[] x) -> int:\n    return len(x)\nint x=[1]\nint n=f(x)", "Cannot assign"),
            ("def f(void[] x) -> void:\n    pass", "parameter cannot be void"),
            ("for x in []:\n    pass\nint y=x", "may not be initialized"),
            ("for len in [1]:\n    pass", "conflicts with a function"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)
