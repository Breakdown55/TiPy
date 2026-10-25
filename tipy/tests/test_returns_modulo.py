import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class ReturnsModuloTests(unittest.TestCase):
    def test_modulo_precedence(self):
        value = compile_source("int x=1+7%3-2")["ast"]["body"][0]["value"]
        self.assertEqual(value["left"]["right"]["kind"], "modulo")
        self.assertEqual(compile_source("int x=7%3+1")["ast"]["body"][0]["value"]["left"]["kind"], "modulo")

    def test_modulo_two_uses_mask_without_division_helper(self):
        assembly = compile_source("int x=-3\nint even=x%2")["assembly"]
        self.assertIn("and 1", assembly)
        self.assertNotIn("__math_mod:", assembly)

    def test_array_return_owns_its_result(self):
        result = compile_source("def f() -> int[]:\n    return [1,2]\nint x=f()")
        self.assertEqual(result["symbols"]["x"]["type"], "i16[]")
        self.assertTrue(result["checked"]["body"][1]["value"]["owned"])

    def test_invalid_types_and_zero_divisor(self):
        for source, error in [
            ("int x=3%0", "divisor cannot be zero"),
            ("int x=True%2", "require integers"),
            ("def f() -> int:\n    return [2]", "Cannot assign i16\\[\\] to i16"),
            ("def f() -> void[]:\n    return []", "void elements"),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, error):
                compile_source(source)
