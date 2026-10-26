import unittest

from tipy.tests.reference import compile_project
from tipy.errors import CompileError


class ProjectTests(unittest.TestCase):
    def test_function_values_resolve_in_entry_and_imported_modules(self):
        utility='def triple(int x):\n    return x*3'
        for source in ('def triple(int x):\n    return x*3\nvalues=list(map(triple,[1,2]))',
                       'import utils\ncallback=utils.triple\nvalues=list(map(callback,[1,2]))',
                       'from utils import triple as times_three\nvalues=list(map(times_three,[1,2]))'):
            result=compile_project({'main.tip':source,'utils.tip':utility},'main.tip')
            self.assertEqual(result['symbols']['values']['type'],'i16[]')

    def test_lexical_function_values_shadow_exports(self):
        source='def triple(int x):\n    return x*3\ndef outer():\n    def triple(int x):\n        return x+1\n    return list(map(triple,[1,2]))\nvalues=outer()\nnumbers=[triple for triple in [4,5]]'
        result=compile_project({'main.tip':source},'main.tip')
        self.assertEqual(result['symbols']['values']['type'],'i16[]')
        self.assertEqual(result['symbols']['numbers']['type'],'i16[]')

    def test_qualified_array_return_and_existing_bare_calls(self):
        utils = "def get_evens(int[] x) -> int[]:\n    int evens=[]\n    for num in x:\n        if num%2==0:\n            evens.append(num)\n    return evens"
        for call in ["utils.get_evens(x)", "get_evens(x)"]:
            result = compile_project({"main.tip": f"import utils\nint x=[1,2,3,4,5]\nint evens={call}\nclear()\nfor even in evens:\n    pixel_on(even,0)\nrender()", "utils.tip": utils}, "main.tip")
            self.assertEqual(result["symbols"]["evens"]["type"], "i16[]")
            self.assertIn("call __fn_0", result["assembly"])

    def test_two_modules_can_export_same_name_and_resolve_internal_calls(self):
        files = {"main.tip": "import a\nimport b\nint x=a.f()\nint y=b.f()",
                 "a.tip": "def f() -> int:\n    return g()\ndef g() -> int:\n    return 1",
                 "b.tip": "def f() -> int:\n    return 2"}
        compile_project(files, "main.tip")
        files["main.tip"] += "\nint z=f()"
        with self.assertRaisesRegex(CompileError, "Ambiguous function"):
            compile_project(files, "main.tip")

    def test_module_errors_keep_source_locations(self):
        with self.assertRaisesRegex(CompileError, "has no function") as caught:
            compile_project({"main.tip": "import utils\nint x=utils.missing()", "utils.tip": ""}, "main.tip")
        self.assertEqual((caught.exception.filename, caught.exception.line), ("main.tip", 2))
        with self.assertRaisesRegex(CompileError, "imported module name"):
            compile_project({"main.tip": "import utils\nint utils=[]", "utils.tip": ""}, "main.tip")

    def test_selected_entry_and_import(self):
        result = compile_project({"main.tip": "invalid", "other.tip": "import utils\nint A=B+4",
                                  "utils.tip": "int B=3"}, "other.tip")
        self.assertEqual(list(result["symbols"]), ["B", "A"])

    def test_import_errors_keep_original_file_and_line(self):
        for text in ["# comment\nint B=missing", "# comment\nint B=1;"]:
            with self.subTest(text=text), self.assertRaises(CompileError) as caught:
                compile_project({"main.tip": "import utils", "utils.tip": text}, "main.tip")
            self.assertEqual(caught.exception.filename, "utils.tip")
            self.assertEqual(caught.exception.line, 2)

    def test_missing_and_circular_imports(self):
        with self.assertRaisesRegex(CompileError, "File not found"):
            compile_project({"main.tip": "import utils"}, "main.tip")
        with self.assertRaisesRegex(CompileError, "Circular import"):
            compile_project({"main.tip": "import utils", "utils.tip": "import main"}, "main.tip")
