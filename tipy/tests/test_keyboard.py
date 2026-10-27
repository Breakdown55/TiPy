import unittest
from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class KeyboardTests(unittest.TestCase):
    def test_keyboard_is_native_and_demand_linked(self):
        assembly=compile_source('bool held=key_down("LEFT")')['assembly']
        self.assertIn('__native_key_down:',assembly)
        self.assertIn('in a,(1)',assembly)
        self.assertNotIn('__heap_start:',assembly)
        self.assertNotIn('__native_key_down:',compile_source('int x=1')['assembly'])

    def test_keyboard_rejects_bad_arguments(self):
        for source in ['key_down()', 'key_down(1,2)', 'key_down("unknown")',
                       'key_down(True)', 'key_down(0.5)', 'str s="left"\nkey_down(s)']:
            with self.subTest(source=source),self.assertRaises(CompileError):compile_source(source)

    def test_boolean_return_inference_and_unused_function_pruning(self):
        result=compile_source('def held():\n    return key_down("left")\nbool down=held()')
        self.assertIn('__native_key_down:',result['assembly'])
        from tipy import compile_source as optimized_compile
        assembly=optimized_compile('def unused():\n    return key_down("left")\nint x=1')['assembly']
        self.assertNotIn('__native_key_down:',assembly)
