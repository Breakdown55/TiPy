import unittest

from tipy import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError


UTILS = "def get_evens(int[] x) -> int[]:\n    return [n for n in x if n%2==0]\ndef other() -> int:\n    return 9"


class ImportsDefinesTests(unittest.TestCase):
    def test_exact_user_example_compiles_to_a_uniform_stripe_fill(self):
        main = "from utils import get_evens\n\n#define HEIGHT 64\n\nint x = list(range(0,96))\n\nclear()\nfor col in range(0, 96, 2):\n    for pixel in range(HEIGHT):\n        pixel_on(col, pixel)\n\nrender()"
        result = compile_project({"main.tip": main, "utils.tip": UTILS}, "main.tip")
        self.assertEqual(result["symbols"], {})
        self.assertIn("ld (hl),170", result["assembly"])
        self.assertNotIn("__heap_start", result["assembly"])
        self.assertNotIn("__fn_", result["assembly"])

    def test_selected_functions_and_aliases_resolve(self):
        for clause, call in [("get_evens", "get_evens"), ("get_evens as evens, other as extra", "evens")]:
            result = compile_project({"main.tip": f"from utils import {clause}\nint values={call}([1,2,3,4])",
                                      "utils.tip": UTILS}, "main.tip", optimization="reference")
            self.assertIn("call __fn_0", result["assembly"])
        with self.assertRaisesRegex(CompileError, "has not been defined"):
            compile_project({"main.tip": "from utils import get_evens\nint x=other()", "utils.tip": UTILS}, "main.tip")

    def test_import_diagnostics_point_to_the_import_line(self):
        for clause, message in [("missing", "has no function"), ("*", "Use from module")]:
            with self.subTest(clause=clause), self.assertRaisesRegex(CompileError, message) as caught:
                compile_project({"main.tip": f"# comment\nfrom utils import {clause}", "utils.tip": UTILS}, "main.tip")
            self.assertEqual((caught.exception.filename, caught.exception.line), ("main.tip", 2))

    def test_define_expressions_are_grouped_and_create_no_storage(self):
        result = compile_source("#define BASE 2\n#define HEIGHT BASE + 3\nint x=HEIGHT*2", optimization="reference")
        self.assertEqual(set(result["symbols"]), {"x"})
        from tipy.constant_evaluator import Evaluator
        evaluator = Evaluator(result["checked"])
        evaluator.block(result["checked"]["body"])
        self.assertEqual(evaluator.globals[result["symbols"]["x"]["label"]], 10)

    def test_defines_are_file_scoped(self):
        files = {"main.tip": "from utils import f\n#define HEIGHT 64\nint a=HEIGHT\nint b=f()",
                 "utils.tip": "#define HEIGHT 2\ndef f() -> int:\n    return HEIGHT"}
        result = compile_project(files, "main.tip", optimization="balanced")
        self.assertIn("ld hl,64", result["assembly"])
        self.assertIn("ld hl,2", result["assembly"])

    def test_define_errors_keep_original_locations(self):
        for text in ["#define A B\n#define B A\nint x=A", "#define A 1\n#define A 2\nint x=A",
                     "#define HEIGHT\nint x=1", "#define A MISSING\nint x=A"]:
            with self.subTest(text=text), self.assertRaises(CompileError) as caught:
                compile_project({"main.tip": text}, "main.tip")
            self.assertEqual(caught.exception.filename, "main.tip")
            self.assertGreaterEqual(caught.exception.line, 1)

    def test_expanded_errors_point_to_usage(self):
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": "#define HEIGHT 40000\nint x=HEIGHT"}, "main.tip")
        self.assertEqual((caught.exception.filename, caught.exception.line, caught.exception.column), ("main.tip", 2, 7))
