import unittest
from pathlib import Path

from tipy import compile_source
from tipy.errors import CompileError
from tipy.game_api import _scene_partition, constant
from tipy.graphics_library import walk
from tipy.project import compile_project

ROOT = Path(__file__).resolve().parents[2]
ASSET = (ROOT / "examples/lumen_gallery.obj").read_text(encoding="utf-8")


class GameApiTests(unittest.TestCase):
    def test_gallery_uses_static_native_geometry_without_a_heap(self):
        result = compile_project(
            {
                "main.tip": (ROOT / "examples/lumen_gallery.tip").read_text(
                    encoding="utf-8"
                ),
                "lumen_gallery.obj": ASSET,
            },
            "main.tip",
        )
        self.assertNotIn("__heap_start:", result["assembly"])
        self.assertIn("__gfx_mesh_vertices_0:", result["assembly"])
        self.assertIn("__gfx_mesh_collides_q4:", result["assembly"])

    def test_unused_game_helpers_add_no_runtime_or_asset_dependency(self):
        result = compile_source(
            'int scene=load_scene("unused.obj")\n'
            "int player=first_person()\nint sun=orbit_light()\nint answer=42"
        )
        self.assertNotIn("__gfx_", result["assembly"])
        self.assertNotIn("__native_", result["assembly"])
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_decimal_setup_uses_world_units_without_float_allocations(self):
        result = compile_source(
            "int player=first_person(x=-1.5,z=2.25,yaw=45)\n"
            "bool close=player_near(player,-1.5,2.25,radius=0.5)",
            optimization="reference",
        )
        positions = {
            node["name"]: constant(node["value"])
            for node in result["checked"]["body"]
            if node.get("kind") == "declare"
            and node["name"].endswith(("_x", "_z", "_yaw"))
        }
        # The public values are world units; only the compiler sees Q4.
        self.assertEqual(positions["__tipy_game_0_x"], -24)
        self.assertEqual(positions["__tipy_game_0_z"], 36)
        self.assertEqual(positions["__tipy_game_0_yaw"], 16)
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_scene_rejects_misspelled_names_and_reports_the_source_location(self):
        source = '\nint scene=load_scene("level.obj",background="room")\nset_visible(scene,"typo",False)'
        with self.assertRaises(CompileError) as raised:
            compile_project({"main.tip": source, "level.obj": ASSET}, "main.tip")
        self.assertEqual(raised.exception.filename, "main.tip")
        self.assertEqual(raised.exception.line, 3)
        self.assertIn("typo", raised.exception.message)

    def test_unsafe_or_ambiguous_handle_use_fails_at_compile_time(self):
        cases = [
            "int player=first_person()\nint copy=player",
            "int player=first_person()\nplayer=3",
            "int player=first_person(x=-121)\nbool close=player_near(player,0,0)",
            "int player=first_person(speed=0)\nbool close=player_near(player,0,0)",
            "int player=first_person()\nbool close=player_near(123,0,0)",
            "int player=first_person()\nbool close=player_near(player,-121,0)",
            "int player=first_person()\nplayer, other=(1,2)",
            "int player=first_person()\nfor player in range(2):\n    pass",
            "int player=first_person(x=1,x=2)\nbool close=player_near(player,0,0)",
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(CompileError):
                compile_source(source)

    def test_partition_is_derived_from_bounds_instead_of_object_names(self):
        def item(name, x1, x2):
            return {
                "name": name,
                "bounds": [(x1, x2), (-2, 2), (-4, 4)],
                "shadow": True,
            }

        west, wall, east = item("a", -8, -6), item("b", 3, 3), item("c", 8, 12)
        partition = _scene_partition([east, west, wall], {"line": 1, "column": 1})
        self.assertEqual((partition["axis"], partition["plane"]), (0, 3))
        self.assertIs(partition["separator"], wall)
        self.assertEqual(partition["negative"], [west])
        self.assertEqual(partition["positive"], [east])
        self.assertFalse(wall["shadow"])
        with self.assertRaisesRegex(CompileError, "overlap"):
            _scene_partition(
                [item("a", -4, 4), item("b", -2, 8)], {"line": 1, "column": 1}
            )

    def test_mutable_visibility_and_frame_clock_remain_bounded(self):
        result = compile_source(
            'int scene=load_scene("level.obj",background="room",unlit=["panel","crystal"])\n'
            "int player=first_person()\nint sun=orbit_light()\n"
            'while not key_down("clear"):\n'
            "    update_player(player,scene)\n"
            "    if player_near(player,14,-8):\n"
            '        set_visible(scene,"crystal",False)\n'
            "    clear()\n    draw_scene(scene,camera=player,light=sun)\n    render()",
            assets={"level.obj": ASSET},
        )
        frame_steps = [
            node
            for node in walk(result["checked"])
            if node.get("kind") == "native_graphics"
            and node.get("name") == "mesh_frame_step"
        ]
        self.assertEqual(len(frame_steps), 1)
        visibility = [name for name in result["symbols"] if "_visible_" in name]
        self.assertEqual(len(visibility), 1)

    def test_nested_and_sequential_frame_loops_keep_their_own_updates(self):
        result = compile_source(
            'int scene=load_scene("level.obj",background="room")\n'
            "int player=first_person()\n"
            "for group in range(2):\n"
            "    for frame in range(3):\n"
            "        update_player(player,scene)\n"
            "        render()\n"
            "for later in range(2):\n"
            "    update_player(player,scene)\n    render()",
            assets={"level.obj": ASSET},
        )
        loops = [
            node for node in result["checked"]["body"] if node.get("kind") == "for"
        ]
        self.assertEqual(loops[0]["body"][0]["kind"], "for")
        for loop in (loops[0]["body"][0], loops[1]):
            self.assertEqual(loop["body"][0]["value"]["name"], "mesh_frame_step")


if __name__ == "__main__":
    unittest.main()
