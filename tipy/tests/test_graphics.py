import unittest
from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class GraphicsTests(unittest.TestCase):
    def test_pixels_use_native_runtime_without_list_heap(self):
        assembly = compile_source("pixel_on(1,2)\nflip_pixel(1,2)\npixel_off(1,2)")["assembly"]
        self.assertNotIn("dw 0x486a", assembly)
        self.assertIn("__gfx_pixel:", assembly)
        self.assertNotIn("__heap_start", assembly)

    def test_invalid_pixel_calls(self):
        for source, error in [
            ("pixel_on(1)", "two coordinates"),
            ("pixel_off(True,2)", "must be integers"),
            ("flip_pixel([],2)", "must be integers"),
            ("int x=pixel_on(1,2)", "void function"),
            ("def pixel_on() -> void:\n    pass", "reserved graphics builtin"),
            ("int flip_pixel=1", "function name"),
            ("clear(1)", "expects 0 arguments"),
            ("line(1,2)", "expects 4 arguments"),
            ("rectangle(1,2,3,4,1,1)", "filled must be bool"),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(CompileError, error):
                compile_source(source)

    def test_only_render_copies_to_the_lcd(self):
        assembly = compile_source("clear()\npixel_on(1,2)\nline(0,0,95,63)\nrectangle(1,5,2,6,1,True)\nrender()")["assembly"]
        self.assertEqual(assembly.count("dw 0x486a"), 1)
        self.assertNotIn("__arr_", assembly)
        self.assertIn("__gfx_rectangle:", assembly)
