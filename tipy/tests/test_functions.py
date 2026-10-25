import json
import unittest

from tipy.tests.reference import compile_source
from tipy.errors import CompileError
from tipy.tests.reference import compile_project


class FunctionTests(unittest.TestCase):
    def test_typed_signature_and_per_call_local_layout(self):
        result = compile_source("def add(int x, int y) -> int:\n    int result=x+y\n    return result\nint total=add(3,4)")
        function = result["checked"]["body"][0]
        self.assertEqual(function["return_type"], "i16")
        self.assertEqual(function["locals"]["x"]["offset"], 6)
        self.assertEqual(function["locals"]["y"]["offset"], 4)
        self.assertEqual(function["locals"]["result"]["offset"], -2)
        self.assertEqual(function["frame_size"], 2)
        self.assertEqual(list(result["symbols"]), ["total"])
        self.assertIn("push ix", result["assembly"])
        self.assertIn("ld sp,ix", result["assembly"])
        json.dumps(result)

    def test_forward_call_and_both_branches_return(self):
        compile_source("int A=choose(True)\ndef choose(bool flag) -> int:\n    if flag:\n        return 1\n    else:\n        return 2")

    def test_void_and_early_return(self):
        compile_source("def f() -> void:\n    return\nf()")
        compile_source("def f(int A) -> int:\n    if A>0:\n        return A\n    return -A\nint answer=f(-2)")

    def test_invalid_function_usage(self):
        cases = [
            ("return 1", "return must be inside"),
            ("int A=unknown()", "has not been defined"),
            ("def f(int A) -> int:\n    return A\nint x=f()", "expects 1 arguments"),
            ("def f(int A) -> int:\n    return A\nint x=f(True)", "Cannot assign bool"),
            ("def f() -> int:\n    pass", "every path"),
            ("def f(bool A) -> int:\n    if A:\n        return 1", "every path"),
            ("def f() -> int:\n    return", "must return a value"),
            ("def f() -> void:\n    return 1", "cannot return a value"),
            ("def f() -> void:\n    pass\nint A=f()", "does not produce a value"),
            ("def f(int A,int A) -> int:\n    return A", "Duplicate parameter"),
            ("def f(void A) -> void:\n    pass", "parameter cannot be void"),
            ("def f() -> void:\n    void A=1", "variable cannot be void"),
            ("def f() -> int:\n    int A=A\n    return A", "may not be initialized"),
            ("def f(int A) -> int:\n    int A=2\n    return A", "already declared"),
            ("def f() -> int:\n    break\n    return 1", "break must be inside"),
            ("def f() -> void:\n    def g() -> void:\n        pass", "Nested functions"),
            ("int A=f()\nint B=12\ndef f() -> int:\n    return B", "uninitialized global"),
            ("int A=1\ndef f() -> void:\n    A+=2\nf()", "global writes"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)

    def test_recursive_global_requirements_reach_call_site(self):
        with self.assertRaisesRegex(CompileError, "uninitialized global"):
            compile_source("int A=f()\nint B=3\ndef f() -> int:\n    return g()\ndef g() -> int:\n    return B")

    def test_imported_function_error_location(self):
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": "import utils\nint A=add(True,2)",
                             "utils.tip": "def add(int x,int y) -> int:\n    return x+y"}, "main.tip")
        self.assertEqual(caught.exception.filename, "main.tip")
        self.assertEqual(caught.exception.line, 2)

    def test_frame_limits_produce_compile_errors(self):
        source = "def large() -> void:\n" + "\n".join(f"    int A{i}=0" for i in range(65))
        with self.assertRaisesRegex(CompileError, "128-byte"):
            compile_source(source)
        params = ",".join(f"int A{i}" for i in range(63))
        with self.assertRaisesRegex(CompileError, "62 parameters"):
            compile_source(f"def many({params}) -> void:\n    pass")
