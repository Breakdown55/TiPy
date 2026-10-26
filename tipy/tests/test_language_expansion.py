import unittest

from tipy import compile_source
from tipy.errors import CompileError


class LanguageExpansionTests(unittest.TestCase):
    def compile(self,source):return compile_source(source,optimization="reference")

    def test_strings_and_nested_container_shapes(self):
        r=self.compile('matrix=[[1,2],[3]]\nnames=["a","b"]\npair=(1,"a")\nd={"a":matrix}\ns={1,2}\nview=d.values()\n')
        self.assertEqual({n:s["type"] for n,s in r["symbols"].items()},dict(matrix="i16[][]",names="str[]",pair="tuple[i16,str]",d="dict[str,i16[][]]",s="set[i16]",view="view[values,dict[str,i16[][]]]"))

    def test_native_numeric_types_keep_int_as_i16(self):
        r=self.compile('int a=2\ni32 b=100000\nfloat c=1.1\nd=1/3\ne=128.0')
        self.assertEqual({n:s["type"] for n,s in r["symbols"].items()},dict(a="i16",b="i32",c="float",d="float",e="float"))
        with self.assertRaisesRegex(CompileError,"i16 literal range"):self.compile('x=100000')
        with self.assertRaisesRegex(CompileError,"outside i32"):self.compile('i32 x=2147483648')

    def test_class_fields_are_shared_only_through_instance_aliases(self):
        r=self.compile('class Box:\n    def __init__(self,str text):\n        self.text=text\n    def read(self):\n        return self.text\na=Box("one")\nb=a\ntext=b.read()')
        self.assertEqual(r["symbols"]["a"]["type"],"object[Box]")
        self.assertEqual(r["symbols"]["text"]["type"],"str")

    def test_closure_types_survive_function_return(self):
        r=self.compile('def outer(int x):\n    def inner(int y):\n        return x+y\n    return inner\nf=outer(3)\nanswer=f(2)')
        self.assertTrue(r["symbols"]["f"]["type"].startswith("closure["))
        self.assertEqual(r["symbols"]["answer"]["type"],"i16")

    def test_generator_calls_are_iterators_and_include_native_context_switches(self):
        r=self.compile('def values(int n):\n    for x in range(n):\n        yield x\nv=values(2)\nfirst=next(v)')
        self.assertEqual(r["symbols"]["v"]["type"],"iter[i16,generator,values]")
        self.assertIn("__obj_gen_resume:",r["assembly"])

    def test_unused_features_do_not_link_their_runtimes(self):
        r=self.compile('def unused(str text):\n    return text.split(",")\nint x=1\nx+=2')
        for label in ("__str_split:","__obj_gen_resume:","__obj_gc_collect:","__dict_new:","__heap_start:"):
            self.assertNotIn(label,r["assembly"])

    def test_invalid_features_report_source_errors(self):
        cases=[('a=[1]\na.sort(reverse=1)',"reverse"),('a={1,"x"}',"Cannot assign"),('a=(1,"x")\ni=1\nx=a[i]',"matching"),('def f(int a=1,int b):\n    return a+b',"required parameter"),('def f(int a):\n    return a\nx=f(a=1,a=2)',"Repeated"),('nonlocal x',"nonlocal"),('yield 1',"yield requires"),('raise',"active exception"),('x=next([1])',"iterator")]
        for source,message in cases:
            with self.subTest(source=source),self.assertRaisesRegex(CompileError,message):self.compile(source)

    def test_optional_values_remain_distinct_from_integer_zero(self):
        r=self.compile('x=None\nx=0\nbool absent=x is None\nvalue=0\nif x is not None:\n    value=x+1')
        self.assertEqual(r["symbols"]["x"]["type"],"optional[i16]")
        self.assertEqual(r["symbols"]["value"]["type"],"i16")
