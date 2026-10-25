import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class ListTests(unittest.TestCase):
    def test_only_reachable_runtime_routines_are_emitted(self):
        assembly = compile_source("int values=[1,2,3]\nint size=len(values)")["assembly"]
        self.assertIn("__arr_append:", assembly)
        self.assertIn("__arr_malloc:", assembly)
        self.assertIn("__arr_reserve:", assembly)
        self.assertNotIn("__arr_insert:", assembly)
        self.assertNotIn("__arr_reverse:", assembly)
        self.assertNotIn("__arr_pop:", assembly)
        self.assertNotIn("__arr_get:", assembly)

    def test_initializer_infers_array_without_brackets_on_type(self):
        result = compile_source("int scores=[10,20,30]\nint x=scores[1]\nscores[2]=40\nscores.append(50)\nint size=len(scores)")
        self.assertEqual(result["symbols"]["scores"]["type"], "i16[]")

    def test_postfix_after_multiline_closing_delimiter(self):
        result = compile_source("int scores = ([\n    1,\n    2,\n]).copy()\nint size=len(scores)")
        self.assertEqual(result["symbols"]["scores"]["size"], 2)
        self.assertNotIn("call __arr_len", result["assembly"])
        self.assertIn("__heap_start:", result["assembly"])

    def test_empty_byte_bool_and_alias_types(self):
        result = compile_source("u8 bytes=[]\nbytes.extend([1,255])\nbool flags=[True,False]\nbool other=flags\nint size=len(other)")
        self.assertEqual(result["symbols"]["bytes"]["type"], "u8[]")
        self.assertEqual(result["symbols"]["other"]["type"], "bool[]")

    def test_local_array_slots_and_cleanup(self):
        result = compile_source("def f() -> int:\n    u8 values=[1,2]\n    return values.pop()\nint answer=f()")
        function = result["checked"]["body"][0]
        self.assertEqual(function["locals"]["values"]["type"], "u8[]")
        self.assertIn("call __arr_release", result["assembly"])

    def test_multiline_initializers_and_comments(self):
        result = compile_source("int scores = [\n    1, # first\n    2,\n]\nscores.append(\n    3\n)")
        self.assertEqual(result["symbols"]["scores"]["type"], "i16[]")

    def test_invalid_list_operations(self):
        cases = [
            ("int x=1\nx.append(2)", "require an array"),
            ("int a=[True]", "Cannot assign bool"),
            ("u8 a=[256]", "outside u8"),
            ("bool a=[1]", "Cannot assign"),
            ("int a=[1]\na=False", "Cannot assign"),
            ("int a=1\na=[1]", "Cannot assign"),
            ("int a=[1]\nint x=a[True]", "index must be an integer"),
            ("int a=[1]\na.insert(True,2)", "index must be an integer"),
            ("int a=[1]\na.append(True)", "Cannot assign"),
            ("int a=[1]\na.append()", "argument count"),
            ("int a=[1]\na.sort()", "Unsupported array method"),
            ("int a=[1]\nint x=a+1", "scalar operators"),
            ("int a=[1]\na[0]+=a", "requires integers"),
            ("int a=[1]\nif a:\n    pass", "must be bool"),
            ("int a=[[1]]", "Cannot assign"),
            ("def f(int x) -> int:\n    return x\nint a=[1]\nint x=f(a)", "Cannot assign"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)
