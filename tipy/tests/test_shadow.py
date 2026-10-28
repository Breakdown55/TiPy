import unittest

from tipy import compile_source
from tipy.drawing_optimizer import annotate_legacy_lighting


class ShadowCompilerTests(unittest.TestCase):
    def test_unused_shadows_are_not_linked(self):
        assembly = compile_source("pixel_on(0, 0)")["assembly"]
        self.assertNotIn("__gfx_mesh_shadow:", assembly)
        self.assertNotIn("__gfx_shadow_active:", assembly)

    def test_legacy_light_remains_reachable_through_conditional_calls(self):
        def native(name):
            return {"kind": "native_graphics", "name": name}

        before = native("mesh_draw")
        after = native("mesh_draw")
        tree = {
            "body": [
                native("solid_no_lights"),
                before,
                {
                    "kind": "conditional",
                    "condition": {"kind": "boolean", "value": True},
                    "body": {"kind": "call", "symbol": "user_function"},
                    "alternative": {"kind": "number", "value": 0},
                },
                after,
            ]
        }
        annotate_legacy_lighting(tree)
        self.assertFalse(before["legacy_lighting"])
        self.assertTrue(after["legacy_lighting"])

    def test_both_conditional_draw_branches_are_analyzed(self):
        yes = {"kind": "native_graphics", "name": "mesh_draw"}
        no = {"kind": "native_graphics", "name": "mesh_draw"}
        tree = {
            "body": [
                {"kind": "conditional", "condition": {}, "body": yes, "alternative": no}
            ]
        }
        annotate_legacy_lighting(tree)
        self.assertTrue(yes["legacy_lighting"])
        self.assertTrue(no["legacy_lighting"])
