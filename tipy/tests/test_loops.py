import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError
from tipy.tests.reference import compile_project


class LoopTests(unittest.TestCase):
    def test_loop_and_control_statements_in_tree(self):
        result = compile_source("int A=0\nwhile A<10:\n    A+=1\n    if A==3:\n        continue\n    if A==5:\n        break")
        loop = result["ast"]["body"][1]
        self.assertEqual(loop["kind"], "while")
        self.assertEqual(loop["body"][1]["body"][0]["kind"], "continue")
        self.assertEqual(loop["body"][2]["body"][0]["kind"], "break")
        self.assertIn("; Line 2: while", result["assembly"])

    def test_invalid_loop_usage(self):
        cases = [
            ("break", "break must be inside"),
            ("continue", "continue must be inside"),
            ("if True:\n    break", "break must be inside"),
            ("while 1:\n    pass", "while condition must be bool"),
            ("while True:\npass", "Expected indent"),
            ("while False:\n    int A=1\nint B=A", "may not be initialized"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)

    def test_loop_declared_variable_can_be_used_inside_body(self):
        compile_source("int A=0\nwhile A<3:\n    int value=A+1\n    A=value")

    def test_imported_loop_error_preserves_location(self):
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": "import utils", "utils.tip": "# comment\ncontinue"}, "main.tip")
        self.assertEqual(caught.exception.filename, "utils.tip")
        self.assertEqual(caught.exception.line, 2)
