import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError
from tipy.lexer import tokenize
from tipy.parser import parse


class CompilerTests(unittest.TestCase):
    def test_tokenizer_ignores_spacing_and_comments(self):
        tokens = tokenize("int A=3+4 # an initializer\r\nA += 1")
        self.assertEqual([t["kind"] for t in tokens], [
            "TYPE", "NAME", "OP", "NUMBER", "OP", "NUMBER", "NEWLINE",
            "NAME", "OP", "NUMBER", "NEWLINE", "EOF"])
        self.assertEqual(tokens[7]["line"], 2)

    def test_parser_groups_parentheses_and_left_associative_subtraction(self):
        tree = parse(tokenize("int A = 3 - 4 - (5 + 6)"))
        value = tree["body"][0]["value"]
        self.assertEqual(value["kind"], "subtract")
        self.assertEqual(value["left"]["kind"], "subtract")
        self.assertEqual(value["right"]["kind"], "add")

    def test_checker_resolves_alias_without_mutating_parser_tree(self):
        result = compile_source("int A = 48\nA += 1")
        self.assertEqual(result["ast"]["body"][0]["type"], "int")
        self.assertEqual(result["checked"]["body"][0]["type"], "i16")
        self.assertEqual(len(result["symbols"]), 1)
        self.assertEqual(result["checked"]["body"][1]["symbol"], result["symbols"]["A"]["label"])

    def test_stages_are_json_serializable(self):
        self.assertIn('"kind": "declare"', json.dumps(compile_source("int A = 1")))

    def test_nested_expression_has_balanced_stack_and_one_storage_slot(self):
        assembly = compile_source("int A = 1 + (2 - (3 + 4))\nA += 1")["assembly"]
        self.assertEqual(assembly.count("push hl"), assembly.count("pop hl"))
        self.assertLess(assembly.count("push hl"), 4)
        self.assertEqual(assembly.count(": dw 0"), 1)
        self.assertLess(assembly.index("        ret"), assembly.index("; Variable storage"))

    def test_storage_sizes_and_case_sensitive_names(self):
        symbols = compile_source("int A=1\ni16 a=2\nu8 byte=255\nbool ready=True")["symbols"]
        self.assertEqual([v["size"] for v in symbols.values()], [2, 2, 1, 1])
        self.assertEqual(len({v["label"] for v in symbols.values()}), 4)

    def test_signed_minimum(self):
        self.assertIn("ld hl,32768", compile_source("int A=-32768")["assembly"])

    def test_invalid_programs_have_source_errors(self):
        cases = [
            ("int A=1;", "Semicolons"),
            ("int A=", "Expected a number"),
            ("int A=(1+2", r"Expected \)"),
            ("A+=1", "not been declared"),
            ("int A=A", "not been declared"),
            ("int A=1\nint A=2", "already declared"),
            ("int A=32768", "outside i16"),
            ("int A=-32769", "outside i16"),
            ("int A=32767+1", "outside i16"),
            ("u8 A=256", "outside u8"),
            ("u8 A=-1", "outside u8"),
            ("bool A=1", "Cannot assign"),
            ("int A=True", "Cannot assign"),
            ("int A=True+1", "require integers"),
            ("bool A=-False", "Cannot negate"),
            ("int A=1\nu8 B=A", "Cannot narrow"),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(CompileError, message) as caught:
                    compile_source(source)
                self.assertGreaterEqual(caught.exception.line, 1)
                self.assertGreaterEqual(caught.exception.column, 1)

    def test_cli_output_and_diagnostics(self):
        # Keep child-process output inside the same writable workspace.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            output = Path(directory) / "main.asm"
            command = [sys.executable, "-m", "tipy", "-", "--optimization", "reference", "-o", str(output)]
            success = subprocess.run(command, input="int A=7", text=True, capture_output=True)
            self.assertEqual(success.returncode, 0, success.stderr)
            original = output.read_text()
            self.assertIn("ld hl,7", original)
            failed = subprocess.run(command, input="int A=B", text=True, capture_output=True)
            self.assertEqual(failed.returncode, 1)
            self.assertIn("-:1:", failed.stderr)
            self.assertEqual(output.read_text(), original)

    def test_cli_can_inspect_parser_output_before_name_checking(self):
        result = subprocess.run([sys.executable, "-m", "tipy", "-", "--show", "ast"],
                                input="int A = missing", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["body"][0]["value"]["name"], "missing")


if __name__ == "__main__":
    unittest.main()
