import unittest

from tipy import compile_source
from tipy.framebuffer_backend import lower_frames, patch_code


class AggressiveOptimizerTests(unittest.TestCase):
    def test_no_observable_output_eliminates_functions_lists_and_heap(self):
        result = compile_source("def f(int n) -> int:\n    return n+1\nint x=f(6)\nint values=list(range(30))")
        self.assertEqual(result["symbols"], {})
        self.assertEqual(set(result["source_symbols"]), {"x", "values"})
        self.assertNotIn("__heap", result["assembly"])
        self.assertNotIn("__fn_", result["assembly"])
        self.assertEqual(result["assembly"].count("        ret"), 1)

    def test_unrendered_graphics_are_dead(self):
        result = compile_source("clear()\npixel_on(2,3)")
        self.assertNotIn("0x9340", result["assembly"])
        self.assertNotIn("0x486a", result["assembly"])

    def test_checkerboard_becomes_a_24_byte_pattern_without_heap(self):
        source = "int evens=[n for n in range(96) if n%2==0]\nint odds=[n for n in range(96) if n%2!=0]\nclear()\nfor row in range(64):\n    if row%2==0:\n        for x in evens:\n            pixel_on(x,row)\n    else:\n        for x in odds:\n            pixel_on(x,row)\nrender()"
        result = compile_source(source)
        frame = result["optimized"]["static_frames"][0]
        self.assertEqual(frame["encoding"], "pattern")
        self.assertEqual(frame["data"], [170]*12+[85]*12)
        self.assertNotIn("__heap", result["assembly"])
        self.assertEqual(result["symbols"], {})

    def test_balanced_mode_evaluates_filter_functions_and_materializes_data_once(self):
        source = "def evens(int[] xs) -> int[]:\n    int out=[]\n    for x in xs:\n        if x%2==0:\n            out.append(x)\n    return out\nint values=evens([1,2,3,4])"
        result = compile_source(source, optimization="balanced")
        self.assertEqual(result["optimized"]["body"][0]["value"]["values"], [2,4])
        self.assertIn("__arr_from_data:", result["assembly"])
        self.assertNotIn("__fn_", result["assembly"])
        self.assertNotIn("__arr_append:", result["assembly"])

    def test_infinite_work_is_not_mistaken_for_a_terminating_program(self):
        result = compile_source("while True:\n    pass")
        self.assertNotIn("static_frames", result["optimized"])
        self.assertTrue(any(item.get("applied") is False for item in result["optimization_report"]))
        self.assertIn("jp __L", result["assembly"])

    def test_runtime_errors_are_not_erased(self):
        for source in ["int x=[1]\nint y=x[5]", "int z=0\nint x=1//z", "int a=min([])"]:
            with self.subTest(source=source):
                result = compile_source(source)
                self.assertNotIn("static_frames", result["optimized"])
                self.assertIn("__list_error", result["assembly"])

    def test_multiple_render_boundaries_are_preserved(self):
        result = compile_source("clear()\npixel_on(1,1)\nrender()\nflip_pixel(1,1)\nrender()")
        self.assertEqual(len(result["optimized"]["static_frames"]), 2)
        self.assertEqual(result["assembly"].count("dw 0x486a"), 2)

    def test_symbolic_framebuffer_does_not_assume_zeroed_ram(self):
        result = compile_source("pixel_on(1,1)\nrender()")
        self.assertEqual(result["optimized"]["static_frames"][0]["encoding"], "patches")
        self.assertIn("set 6,(hl)", result["assembly"])
        self.assertNotIn("ldir", result["assembly"])

    def test_transient_mutation_is_not_pure_just_because_values_are_restored(self):
        source = "def touch(int[] a) -> int:\n    a.append(5)\n    int old=a.pop()\n    return 7\nint a=[1]\nint result=touch(a)"
        result = compile_source(source, optimization="balanced")
        self.assertIn("call __fn_0", result["assembly"])

    def test_unknown_branch_invalidates_buffer_knowledge(self):
        source = "def draw(bool flag) -> void:\n    clear()\n    if flag:\n        pixel_on(1,1)\n    clear()\n    render()\ndraw(True)"
        result = compile_source(source, optimization="balanced")
        self.assertEqual(result["assembly"].count("call __gfx_clear"), 2)

    def test_constant_range_reduces_to_a_small_counter_loop_on_fallback(self):
        result = compile_source("for n in range(65):\n    render()")
        self.assertTrue(any(item.get("pass") == "range-strength-reduction" and item.get("specialized") for item in result["optimization_report"]))
        self.assertNotIn("jp __arr_fail", result["assembly"])

    def test_constant_zero_step_fault_is_preserved_after_propagation(self):
        result = compile_source("int step=0\nfor n in range(0,4,step):\n    for x in [1,2]:\n        pixel_on(x,n)\nrender()")
        self.assertIn("jp __arr_fail", result["assembly"])

    def test_dead_drawing_inside_infinite_loop_does_not_change_termination(self):
        result = compile_source("while True:\n    pixel_on(1,1)")
        self.assertNotIn("__gfx_pixel", result["assembly"])
        self.assertIn("jp __L", result["assembly"])

    def test_sparse_image_uses_fill_plus_patches_instead_of_a_large_bitmap(self):
        result = compile_source("clear()\npixel_on(1,1)\nrender()")
        self.assertEqual(result["optimized"]["static_frames"][0]["encoding"], "fill_patches")
        self.assertNotIn("__frame_data", result["assembly"])
