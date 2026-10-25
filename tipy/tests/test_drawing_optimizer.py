import unittest
from tipy.tests.reference import compile_source


class DrawingOptimizerTests(unittest.TestCase):
    def test_invariant_row_loop_is_batched_without_mutating_checked_tree(self):
        source = "int xs=[1,3,5]\nfor row in range(64):\n    for x in xs:\n        pixel_on(x,row)"
        result = compile_source(source)
        self.assertIn("__gfx_build_row_mask:", result["assembly"])
        self.assertIn("__gfx_or_row:", result["assembly"])
        self.assertNotIn("__gfx_pixel_on:", result["assembly"])
        self.assertEqual(result["checked"]["body"][1]["body"][0]["kind"], "for")
        original = compile_source(source, optimize_graphics=False)["assembly"]
        self.assertIn("__gfx_pixel_on:", original)
        self.assertNotIn("__gfx_build_row_mask:", original)

    def test_mutation_and_display_updates_are_optimization_barriers(self):
        for extra in ["    xs.append(row)", "    render()"]:
            source = "int xs=[1,3]\nfor row in range(4):\n    for x in xs:\n        pixel_on(x,row)\n" + extra
            self.assertNotIn("__gfx_build_row_mask:", compile_source(source)["assembly"])

    def test_changing_y_inside_inner_loop_is_not_invariant(self):
        source = "int xs=[1,2]\nfor row in range(4):\n    for x in xs:\n        pixel_on(x,x)"
        self.assertNotIn("__gfx_build_row_mask:", compile_source(source)["assembly"])

    def test_same_row_and_pixel_binding_is_not_invariant(self):
        source = "int xs=[1,2]\nfor x in range(4):\n    for x in xs:\n        pixel_on(x,x)"
        self.assertNotIn("__gfx_build_row_mask:", compile_source(source)["assembly"])

    def test_unreachable_loops_are_ignored(self):
        compile_source("def f() -> int:\n    return 1\n    for x in range(3):\n        pass\nint x=f()")

    def test_known_tiny_row_loops_do_not_pay_mask_setup_cost(self):
        source = "int xs=[1]\nfor row in range(1):\n    for x in xs:\n        pixel_on(x,row)"
        self.assertNotIn("__gfx_build_row_mask:", compile_source(source)["assembly"])
