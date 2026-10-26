import unittest

from tipy.compiler import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError


class DictionaryTests(unittest.TestCase):
    def test_literals_infer_homogeneous_types_and_accept_multiline_entries(self):
        result = compile_source('scores={\n    "Alice":10,\n    "Bob":20,\n}\nflags={1:True}\nstr value={"a":"b"}["a"]', optimization="reference")
        self.assertEqual(result["symbols"]["scores"]["type"], "dict[str,i16]")
        self.assertEqual(result["symbols"]["flags"]["type"], "dict[i16,bool]")
        self.assertEqual(result["symbols"]["value"]["type"], "str")

    def test_explicit_empty_types_parameters_and_returns(self):
        compile_source('dict[str,int] scores={}\nscores["A"]=1\ndef copy(dict[str,int] source) -> dict[str,int]:\n    return dict(source)\nother=copy(scores)')
        compile_source('dict[str,str] names=dict()\nnames["A"]="Alice"\nstr name=dict(names)["A"]')

    def test_keys_values_and_items_are_typed_direct_iteration_views(self):
        result = compile_source('d={"A":1}\nfor name,score in d.items():\n    print(name,0,0)\nfor key in d.keys():\n    pass\nfor value in d.values():\n    pass', optimization="reference")
        self.assertEqual(result["symbols"]["name"]["type"], "str")
        self.assertEqual(result["symbols"]["score"]["type"], "i16")

    def test_unreachable_dictionaries_and_unused_methods_are_not_linked(self):
        assembly = compile_source('def unused() -> dict[str,int]:\n    return {"A":1}\nx=1')["assembly"]
        self.assertNotIn("__dict_", assembly)
        self.assertNotIn("__heap_start", assembly)
        assembly = compile_source('d={1:2}\nx=d[1]', optimization="reference")["assembly"]
        self.assertIn("__dict_find:", assembly)
        for name in ("hash_str", "equal_str", "copy", "update", "pop", "get_default"):
            self.assertNotIn(f"__dict_{name}:", assembly)

    def test_hash_is_stable_native_and_does_not_require_a_table(self):
        assembly = compile_source('x=hash("Alice")', optimization="reference")["assembly"]
        self.assertIn("__dict_hash_str:", assembly)
        self.assertNotIn("__dict_new:", assembly)
        self.assertNotIn("__dict_find:", assembly)
        self.assertNotIn("__dict_", compile_source('x=hash(-12)', optimization="reference")["assembly"])

    def test_invalid_types_and_view_usage_have_source_errors(self):
        cases = [('d={"A":1,2:3}', 'Cannot assign'), ('d={1:2,3:True}', 'Cannot assign'),
                 ('d={True:1}', 'keys must'), ('d={1:[2]}', 'values must'),
                 ('d={1:{2:3}}', 'values must'), ('dict[bool,int] d={}', 'Dictionary types'),
                 ('d={1:2}\nd["A"]=3', 'Cannot assign'), ('d={1:2}\nd[1]=True', 'Cannot assign'),
                 ('d={1:2}\nx=d.get(1)', 'explicit default'), ('d={1:2}\nx=d.get(1,False)', 'Cannot assign'),
                 ('d={1:2}\nfor a,b in d:\n    pass', 'requires 1 target'),
                 ('d={1:2}\nfor a in d.items():\n    pass', 'requires 2 target'),
                 ('d={1:2}\nx=d.keys()', 'require a for'), ('d={1:2}\nx=d+1', 'arithmetic'),
                 ('x=[1]\ndel x[0]', 'requires a dictionary'), ('d={1:2}\nx=True in d', 'Cannot assign'),
                 ('d={1:2}\nx=[d for i in range(2)]', 'scalar'), ('x=hash([1])', 'hash expects')]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, message): compile_source(source)

    def test_imported_dictionary_function_retains_original_errors(self):
        with self.assertRaises(CompileError) as caught:
            compile_project({"main.tip": "import utils\nx=utils.make()", "utils.tip": 'def make() -> dict[str,int]:\n    return {"A":True}'}, "main.tip")
        self.assertEqual((caught.exception.filename, caught.exception.line), ("utils.tip", 2))
