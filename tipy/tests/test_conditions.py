import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError
from tipy.tests.reference import compile_project


class ConditionTests(unittest.TestCase):
    def test_parser_comparison_precedence_and_nested_else(self):
        tree = compile_source("int A=1\nif A+2 > 10-3:\n    if A==1:\n        A=0\n    else:\n        A=2\nelse:\n    A=3")["ast"]
        outer = tree["body"][1]
        self.assertEqual(outer["kind"], "if")
        self.assertEqual(outer["condition"]["left"]["kind"], "add")
        self.assertEqual(outer["condition"]["right"]["kind"], "subtract")
        self.assertEqual(len(outer["alternative"]), 1)
        self.assertEqual(len(outer["body"][0]["alternative"]), 1)

    def test_comparisons_produce_bool_and_flags_for_direct_conditions(self):
        result = compile_source("int A=12\nbool big=A>10\nif big:\n    A=0")
        self.assertEqual(result["checked"]["body"][1]["value"]["type"], "bool")
        self.assertIn("jp pe,", result["assembly"])
        self.assertIn("sbc hl,de", result["assembly"])

    def test_blank_lines_comments_and_dedent_at_eof(self):
        compile_source("int A=1\nif A>0:\n\n    # comment\n    A=2\n\nelse:\n    pass")

    def test_invalid_conditions_and_indentation(self):
        cases = [
            ("if 1:\n    pass", "must be bool"),
            ("if True:\npass", "Expected indent"),
            ("if True:\n\tpass", "Use spaces"),
            ("if True:\n    pass\n  pass", "Indentation does not match"),
            ("bool A=True>False", "only support"),
            ("bool A=True==1", "Cannot compare"),
            ("int A=1<2", "Cannot assign bool"),
            ("bool A=1<True<3", "Chained comparisons"),
            ("if True:\n    int A=1\nint B=A", "may not be initialized"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)

    def test_condition_errors_in_imported_files_keep_original_location(self):
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": "import utils", "utils.tip": "int A=1\nif A>0:\n    A=missing"}, "main.tip")
        self.assertEqual(caught.exception.filename, "utils.tip")
        self.assertEqual(caught.exception.line, 3)
