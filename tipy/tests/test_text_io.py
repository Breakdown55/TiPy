import unittest

from tipy.compiler import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError


class TextIOTests(unittest.TestCase):
    def test_input_type_comes_from_declaration_and_cast(self):
        source = 'int number=input("N:",0,0)\nstr text=input("Text:",0,16)\nint cast=int(input("N:",0,32))'
        result = compile_source(source, optimization="reference")
        self.assertEqual(result["symbols"]["number"]["type"], "i16")
        self.assertEqual(result["symbols"]["text"]["type"], "str")
        self.assertEqual(result["symbols"]["cast"]["type"], "i16")
        self.assertIn("__text_parse:", result["assembly"])

    def test_immutable_string_types_and_function_returns(self):
        result = compile_source('str name="hello"\nstr other=name\ndef f(str value) -> str:\n    return value\nstr answer=f(other)', optimization="reference")
        self.assertEqual(result["symbols"]["answer"]["type"], "str")
        compile_source('def read() -> int:\n    return input("N:",0,0)\nint x=read()')

    def test_literals_support_quotes_escapes_and_hash_characters(self):
        compile_source(r'print("Hello, \"TiPy\"! # test\n",0,0)')
        compile_source(r"print('Hello \'friend\'',0,0)")

    def test_print_does_not_link_input_or_heap_for_literals(self):
        assembly = compile_source('print("Hello",0,0)\nrender()')["assembly"]
        self.assertIn("__text_draw:", assembly)
        self.assertNotIn("__text_input:", assembly)
        self.assertNotIn("__heap_start", assembly)
        self.assertNotIn("__native_sleep:", assembly)
        self.assertNotIn("__text_number:", assembly)

    def test_unused_text_functions_link_nothing(self):
        assembly = compile_source('def unused() -> str:\n    return input("Name:",0,0)\npass')["assembly"]
        self.assertNotIn("__text_", assembly)
        self.assertNotIn("__heap_start", assembly)

    def test_invalid_calls_keep_source_locations(self):
        cases = [('print("x",0)', 'expects'), ('input(0,1,2)', 'prompt must be text'),
                 ('print("x",True,0)', 'coordinates'), ('print([1],0,0)', 'print expects'),
                 ('print(1.5,0,0)', 'print expects'), ('str x=1', 'Cannot assign'),
                 ('str x="a"\nx=2', 'Cannot assign'), ('x="a"+1', 'String arithmetic'),
                 ('str items=["x",1]', 'Cannot assign'), ('int x=["a" for n in range(3)]', 'Cannot assign'),
                 ('print("é",0,0)', 'ASCII')]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message): compile_source(source)
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": 'print("x",False,0)'}, "main.tip")
        self.assertEqual(caught.exception.filename, "main.tip")
