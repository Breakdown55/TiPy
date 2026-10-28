import unittest

from tipy import compile_source
from tipy.errors import CompileError
from tipy.graphics_library import walk

MODEL = "int m=mesh([(-2,-2,0),(2,-2,0),(0,2,0)],faces=[(0,1,2,0)])\n"


class FractionalCameraTests(unittest.TestCase):
    def test_fractional_camera_links_translation_correction_only_when_used(self):
        fractional = compile_source(
            MODEL + "camera(0,0,128,0,0,0,coordinate_scale=16)\ndraw_mesh(m)",
            optimization="reference",
        )["assembly"]
        ordinary = compile_source(
            MODEL + "camera(0,0,8,0,0,0)\ndraw_mesh(m)", optimization="reference"
        )["assembly"]
        self.assertIn("__gfx_mesh_look_q4:", fractional)
        self.assertIn("__gfx_mesh_eye_shift:", fractional)
        self.assertNotIn("__gfx_mesh_eye_shift:", ordinary)

    def test_collision_scale_does_not_link_camera_state(self):
        assembly = compile_source(
            "int walls=collision_map([(-2,-2,2,2)])\nbool hit=collides(walls,49,0,coordinate_scale=16)",
            optimization="reference",
        )["assembly"]
        self.assertIn("__gfx_mesh_collides_q4:", assembly)
        self.assertNotIn("__gfx_mesh_eye_shift:", assembly)
        self.assertNotIn("__heap_start:", assembly)

    def test_unit_scale_uses_the_ordinary_camera(self):
        checked = compile_source(
            MODEL + "camera(0,0,8,0,0,0,coordinate_scale=1)\ndraw_mesh(m)",
            optimization="reference",
        )["checked"]
        names = [
            node["name"]
            for node in walk(checked)
            if node.get("kind") == "native_graphics"
        ]
        self.assertIn("mesh_look", names)
        self.assertNotIn("mesh_look_q0", names)

    def test_invalid_or_dynamic_scales_are_rejected(self):
        for scale in ["0", "3", "32", "True", "scale"]:
            with self.subTest(scale=scale), self.assertRaisesRegex(
                CompileError, "coordinate_scale"
            ):
                compile_source(
                    MODEL
                    + "int scale=16\ncamera(0,0,128,0,0,0,coordinate_scale="
                    + scale
                    + ")\ndraw_mesh(m)"
                )
