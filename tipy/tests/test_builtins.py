import unittest
from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class BuiltinTests(unittest.TestCase):
    def test_comprehensions_have_private_typed_targets(self):
        result = compile_source("int n=99\nint values=[n*n for n in range(4) if n%2==0]")
        self.assertEqual(set(result["symbols"]), {"n", "values"})
        self.assertEqual(result["symbols"]["values"]["type"], "i16[]")

    def test_enumerate_and_zip_validate_unpacking(self):
        compile_source("for i,x in enumerate([1,2],10):\n    pass\nfor a,b in zip([1],[2]):\n    pass")
        with self.assertRaisesRegex(CompileError, "requires 2 target"):
            compile_source("for x in enumerate([1]):\n    pass")

    def test_range_and_list_infer_array_and_link_only_used_helpers(self):
        assembly = compile_source("int x=list(range(0,30))")["assembly"]
        self.assertIn("__arr_range:", assembly)
        self.assertNotIn("__arr_copy:", assembly)
        self.assertNotIn("__arr_sum:", assembly)
        self.assertNotIn("__arr_min:", assembly)
        self.assertNotIn("__arr_range:", compile_source("int x=1")["assembly"])

    def test_scalar_builtins_do_not_allocate_a_heap(self):
        assembly = compile_source("int x=abs(-4)\nint y=min(4,2,5)\nint z=max(4,2)")["assembly"]
        self.assertNotIn("__heap_start", assembly)

    def test_unused_functions_do_not_pull_in_builtin_runtime_or_heap(self):
        assembly = compile_source("def unused() -> int:\n    return sum(range(10))\nint x=1")["assembly"]
        self.assertNotIn("__fn_", assembly)
        self.assertNotIn("__arr_", assembly)
        self.assertNotIn("__heap_start", assembly)

    def test_invalid_builtin_arguments(self):
        for source, message in [("int x=range(0,4,0)", "step cannot be zero"),
                                ("int x=range(True)", "must be integers"),
                                ("int x=list(1)", "expects an array"),
                                ("int x=sum(1)", "expects an array"),
                                ("int x=abs(True)", "expects an integer"),
                                ("int range=1\nint x=range(3)", "not callable")]:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)

    def test_range_loops_do_not_materialize_lists_or_add_a_heap(self):
        assembly = compile_source("int total=0\nfor i in range(10):\n    total+=i")["assembly"]
        self.assertNotIn("__heap_start", assembly)
        self.assertNotIn("__arr_range:", assembly)
        self.assertNotIn("__arr_malloc:", assembly)

    def test_aggregate_runtime_is_linked_by_dependency(self):
        assembly = compile_source("int value=sum([1,2,3])")["assembly"]
        self.assertIn("__arr_sum:", assembly)
        self.assertIn("__arr_get:", assembly)
        for unused in ["range", "min", "max", "any", "all", "contains", "copy"]:
            self.assertNotIn(f"__arr_{unused}:", assembly)

    def test_boolean_precedence_and_arithmetic_errors(self):
        result = compile_source("bool x=True or False and not 1>2")["ast"]["body"][0]["value"]
        self.assertEqual(result["kind"], "or")
        self.assertEqual(result["right"]["kind"], "and")
        for source, message in [("int x=1//0", "divisor cannot be zero"),
                                ("int x=32767*2", "outside i16"),
                                ("bool x=1 and True", "require bool"),
                                ("int x=[True for n in range(3)]", "Cannot assign bool"),
                                ("int x=[a for a in 1]", "requires an array"),
                                ("bool x=1 in 3", "requires an array")]:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message):
                compile_source(source)
