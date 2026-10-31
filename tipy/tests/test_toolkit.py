import unittest
from pathlib import Path

from tipy import compile_source
from tipy.project import compile_project
from tipy.assembly_size import estimate
from tipy.errors import CompileError

ROOT = Path(__file__).resolve().parents[2]


class ToolkitTests(unittest.TestCase):
    def test_easy_gx_import_forms_and_shared_backend(self):
        for source in (
            "import egx\negx.clear()\negx.pixel_on(4,5)\negx.flip()",
            "from egx import clear, pixel_on, flip\nclear()\npixel_on(4,5)\nflip()",
            "import egx as e\ne.clear()\ne.pixel_on(4,5)\ne.render()",
        ):
            with self.subTest(source=source):
                actual = compile_source(source, optimization="reference")
                expected = compile_source(
                    "import egx\negx.clear()\negx.pixel_on(4,5)\negx.render()",
                    optimization="reference",
                )
                self.assertEqual(actual["assembly"], expected["assembly"])

    def test_unused_shared_transforms_do_not_pull_in_a_matrix_or_heap(self):
        result = compile_source(
            "import egx\ndef unused():\n    egx.translate(3,4)\n    egx.push_matrix()\negx.pixel_on(1,2)"
        )
        self.assertNotIn("__gfx_model:", result["assembly"])
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_uniform_scale_and_transform_arity_are_checked(self):
        for call in ("scale(1,2)", "translate()", "rotate_y(1,2)", "push_matrix(1)"):
            with self.subTest(call=call), self.assertRaises(CompileError):
                compile_source("import egx\negx." + call)

    def test_static_records_preserve_declared_types(self):
        definition = (
            "class Box:\n"
            "    def __init__(self,int x):\n"
            "        self.x=x\n"
            "    def enabled(self,bool flag):\n"
            "        if flag:\n"
            "            self.x+=1\n"
        )
        for program in (
            "int box=Box(3)",
            "box=Box(True)",
            "box=Box(3)\nbox.enabled(1)",
            "box=Box(3)\nint flag=1\nbox.enabled(flag)",
        ):
            with self.subTest(program=program), self.assertRaises(CompileError):
                compile_source(definition + program, optimization="reference")

    def test_forward_field_reads_keep_normal_constructor_checks(self):
        source = (
            "class Box:\n"
            "    def __init__(self):\n"
            "        self.first=self.second\n"
            "        self.second=3\n"
            "box=Box()\nprint(box.first,0,0)"
        )
        result = compile_source(source, optimization="reference")
        self.assertIn("__heap_start:", result["assembly"])

    def test_scalar_fields_use_the_initializer_type(self):
        result = compile_source(
            "import time\nclass Flag:\n"
            "    def __init__(self):\n"
            "        self.value=time.frame_ticks()>0\n"
            "    def reset(self):\n"
            "        self.value=False\n"
            "flag=Flag()\nflag.reset()\nbool value=flag.value",
            optimization="reference",
        )
        self.assertEqual(result["record_symbols"]["flag"]["value"]["type"], "bool")
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_unused_method_arguments_are_still_checked(self):
        definition = (
            "class Box:\n"
            "    def __init__(self):\n"
            "        self.value=0\n"
            "    def skip(self,int ignored):\n"
            "        self.value+=1\n"
            "box=Box()\n"
        )
        for program in ("box.skip(missing)", "bool wrong=True\nbox.skip(wrong)"):
            with self.subTest(program=program), self.assertRaises(CompileError):
                compile_source(definition + program, optimization="reference")

    def test_unlimited_clock_accepts_implicit_and_explicit_zero(self):
        for call in ("clock.tick()", "clock.tick(0)"):
            with self.subTest(call=call):
                result = compile_source(
                    "from timing import Clock\nclock=Clock()\nint elapsed=" + call,
                    optimization="reference",
                )
                self.assertNotIn("__heap_start:", result["assembly"])

    def test_imported_classes_can_be_used_in_function_annotations(self):
        result = compile_source(
            "from geometry import Vector2\ndef get_x(Vector2 value)->int:\n    return value.x\np=Vector2(7,8)\nint x=get_x(p)",
            optimization="reference",
        )
        self.assertIn("x", result["symbols"])

    def test_source_helpers_are_optional_and_do_not_reserve_game_names(self):
        result = compile_source(
            "def first_person(int x) -> int:\n    return x+1\nint value=first_person(2)"
        )
        self.assertNotIn("__gfx_mesh_game_", result["assembly"])
        self.assertNotIn("__heap_start", result["assembly"])

    def test_imported_records_can_use_static_storage(self):
        result = compile_source(
            "from geometry import Vector2, Rect\np=Vector2(2,3)\np.move_ip(4,5)\nbox=Rect(0,0,20,20)\nbool inside=box.collidepoint(p.x,p.y)",
            optimization="reference",
        )
        self.assertNotIn("__heap_start:", result["assembly"])
        self.assertIn("p", result["record_symbols"])

    def test_project_source_overrides_a_bundled_helper(self):
        result = compile_project(
            {
                "main.tip": "from motion import step\nint value=step(4)",
                "motion.tip": "def step(int x)->int:\n    return x+7",
            },
            "main.tip",
            optimization="reference",
        )
        self.assertIn("ld de,7", result["assembly"])
        self.assertNotIn("__heap_start:", result["assembly"])

    def test_escaping_objects_and_managed_fields_keep_the_heap_abi(self):
        for source in [
            "class Box:\n    def __init__(self,int x):\n        self.x=x\na=Box(3)\nb=a\nb.x=7\nprint(a.x,0,0)",
            "class Box:\n    def __init__(self,int x):\n        self.text=str(x)\na=Box(3)\nprint(a.text,0,0)",
        ]:
            with self.subTest(source=source):
                result = compile_source(source, optimization="reference")
                self.assertIn("__heap_start:", result["assembly"])

    def test_recursive_record_methods_keep_stack_guards(self):
        result = compile_source(
            "class Count:\n    def __init__(self):\n        self.value=0\n    def down(self,int n)->int:\n        if n==0:\n            return self.value\n        self.value+=1\n        return self.down(n-1)\nc=Count()\nint result=c.down(3)",
            optimization="reference",
        )
        self.assertIn("__program_end+256", result["assembly"])

    def test_both_graphics_levels_compile_to_the_same_backend(self):
        a = compile_source(
            "import egx\nimport gx\negx.clear()\negx.pixel_on(4,5)\negx.render()",
            optimization="reference",
        )
        b = compile_source(
            "import gx\nimport egx\ngx.clear()\negx.pixel_on(4,5)\ngx.render()",
            optimization="reference",
        )
        self.assertEqual(a["assembly"], b["assembly"])

    def test_new_examples_compile_and_gallery_keeps_fast_vertices(self):
        for name in ("toolkit_2d.tip", "pipeline_control.tip", "lumen_gallery.tip"):
            with self.subTest(name=name):
                result = compile_project(
                    {
                        "main.tip": (ROOT / "examples" / name).read_text(
                            encoding="utf-8"
                        ),
                        "lumen_gallery.obj": (
                            ROOT / "examples/lumen_gallery.obj"
                        ).read_text(encoding="utf-8"),
                    },
                    "main.tip",
                )
                self.assertNotIn("__heap_start:", result["assembly"])
                if name == "lumen_gallery.tip":
                    self.assertIn("__gfx_mesh_vertices_0:", result["assembly"])
                    self.assertNotIn("__gfx_mesh_frame_step:", result["assembly"])
                    self.assertLessEqual(estimate(result["assembly"]), 24169)


if __name__ == "__main__":
    unittest.main()
