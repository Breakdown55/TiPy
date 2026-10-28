import unittest

from tipy import compile_source
from tipy.errors import CompileError
from tipy.graphics_library import walk
from tipy.project import compile_project

ASSET = """o room
v 10 0 0
v 14 0 0
v 14 4 0
v 10 4 0
f 1 2 3 4
o other
v 0 0 2
v 1 0 2
v 0 1 2
f -3 -2 -1
o collider_wall
v -2 -8 -10
v 2 10 10
"""


class SceneAssetTests(unittest.TestCase):
    def test_named_objects_preserve_authored_units_and_polygon_edges(self):
        result = compile_project(
            {
                "main.tip": 'int room=load_obj("level.obj", object="room", normalize=False, brightness=7)\ndraw_mesh(room,cull=False,depth_test=False,outline=True)',
                "level.obj": ASSET,
            },
            "main.tip",
            optimization="reference",
        )
        checked = compile_source(
            'int room=load_obj("level.obj", object="room", normalize=False, brightness=7)\ndraw_mesh(room)',
            assets={"level.obj": ASSET},
            optimization="reference",
        )["checked"]
        packet = next(
            node for node in walk(checked) if node.get("kind") == "geometry_data"
        )
        self.assertEqual(packet["vertex_count"], 4)
        self.assertEqual(packet["data"][4:6], [4, 0])
        self.assertEqual(packet["data"][10:12], [10, 0])
        self.assertNotIn("__heap_start", result["assembly"])

    def test_collision_boxes_are_packed_without_runtime_lists(self):
        result = compile_source(
            "int walls=collision_map([(-2,-10,2,10)])\nbool hit=collides(walls,0,0)",
            optimization="reference",
        )
        packet = next(
            node for node in walk(result["checked"]) if node.get("collision_map")
        )
        self.assertEqual(packet["data"][:4], [0x42, 0x43, 1, 0])
        self.assertIn("__gfx_mesh_collides:", result["assembly"])
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_invalid_selections_and_collision_bounds_fail_cleanly(self):
        for source in [
            'int m=load_obj("level.obj",object="missing")',
            'int m=load_obj("level.obj",normalize=3)',
            "int m=collision_map([(4,0,2,3)])",
            "int m=collision_map([(-121,0,0,0)])",
        ]:
            with self.subTest(source=source), self.assertRaises(CompileError):
                compile_source(source, assets={"level.obj": ASSET})

    def test_obj_colliders_match_the_same_literal_map(self):
        a = compile_source(
            'int m=collision_map("level.obj")',
            assets={"level.obj": ASSET},
            optimization="reference",
        )
        b = compile_source(
            "int m=collision_map([(-2,-10,2,10)])", optimization="reference"
        )
        data = lambda result: next(
            node["data"]
            for node in walk(result["checked"])
            if node.get("collision_map")
        )
        self.assertEqual(data(a), data(b))

    def test_selected_obj_errors_keep_original_asset_line_numbers(self):
        invalid = ASSET.replace("v 0 1 2", "v nan 1 2")
        with self.assertRaises(CompileError) as error:
            compile_source(
                'int m=load_obj("level.obj",object="other",normalize=False)',
                assets={"level.obj": invalid},
            )
        self.assertEqual(error.exception.filename, "level.obj")
        self.assertEqual(error.exception.line, 10)
