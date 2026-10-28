import unittest
from tipy import compile_source
from tipy.project import compile_project
from tipy.errors import CompileError
from tipy.obj_loader import load

QUAD = "v -1 -1 0\nv 1 -1 0\nv 1 1 0\nv -1 1 0\nf 1 2 3 4\n"
LOCATION = {"line": 1, "column": 1}


class ObjTests(unittest.TestCase):
    def test_quad_negative_indices_and_slash_forms(self):
        a = load(QUAD, "shape.obj", 32, LOCATION)
        b = load(
            QUAD.replace("f 1 2 3 4", "f -4/1/1 -3/2/1 -2/3/1 -1/4/1"),
            "shape.obj",
            32,
            LOCATION,
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a[1]), 2)
        self.assertTrue(all(sum(map(abs, v)) <= 32 for v in a[0]))

    def test_concave_polygon_ear_clipping_preserves_area(self):
        src = "v 0 0 0\nv 4 0 0\nv 4 4 0\nv 2 2 0\nv 0 4 0\nf 1 2 3 4 5"
        v, f = load(src, "concave.obj", 32, LOCATION)
        self.assertEqual(len(f), 3)

        def area(p):
            return sum(
                p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
                for i in range(len(p))
            )

        self.assertEqual(sum(area([v[i] for i in t]) for t in f), area(v))

    def test_project_asset_is_packed_and_obj_text_is_not_emitted(self):
        r = compile_project(
            {
                "main.tip": 'int m=load_obj("shape.obj")\nclear()\ndraw_mesh(m)\nrender()',
                "shape.obj": QUAD,
            },
            "main.tip",
        )
        self.assertIn("__gfx_solid_span:", r["assembly"])
        self.assertNotIn("shape.obj", r["assembly"])
        self.assertNotIn("__heap_start:", r["assembly"])

    def test_wireframe_program_does_not_link_depth_or_shading(self):
        r = compile_source("int m=mesh([(0,0,0),(10,10,10)],[(0,1)])\ndraw_mesh(m)")
        for s in ("__gfx_solid_depth:", "__gfx_solid_span:", "__gfx_solid_patterns:"):
            self.assertNotIn(s, r["assembly"])

    def test_unused_obj_does_not_link_a_renderer(self):
        r = compile_source(
            'def unused():\n    int m=load_obj("shape.obj")\n    draw_mesh(m)\npixel_on(1,2)',
            assets={"shape.obj": QUAD},
        )
        self.assertNotIn("__gfx_solid_", r["assembly"])

    def test_obj_diagnostics_retain_asset_line_number(self):
        with self.assertRaises(CompileError) as error:
            compile_project(
                {
                    "main.tip": 'int m=load_obj("bad.obj")\ndraw_mesh(m)',
                    "bad.obj": "v 0 0 0\nf 0 1 2",
                },
                "main.tip",
            )
        self.assertEqual(error.exception.filename, "bad.obj")
        self.assertEqual(error.exception.line, 2)

    def test_missing_asset_invalid_radius_and_invalid_faces(self):
        for src in (
            'int m=load_obj("missing.obj")',
            'int m=load_obj("shape.obj",radius=0)',
            "int m=mesh([(0,0,0),(1,1,1),(2,2,2)],faces=[(0,1,2)])",
            "int m=mesh([(0,0,0),(1,0,0),(0,1,0)],faces=[(0,1,2,17)])",
        ):
            with self.subTest(source=src), self.assertRaises(CompileError):
                compile_source(src, assets={"shape.obj": QUAD})

    def test_malformed_obj_inputs_fail_cleanly(self):
        for src in (
            "v nan 0 0\nf 1 1 1",
            "v 1 0 0 0\nf 1 1 1",
            QUAD.replace("f 1 2 3 4", "f 1 9 3"),
            QUAD.replace("f 1 2 3 4", "f 1 2 1 4"),
            QUAD.replace("f 1 2 3 4", "curv 1 2 3"),
            "v 0 0 0\n" * 65 + "f 1 2 3",
        ):
            with self.subTest(source=src), self.assertRaises(CompileError):
                load(src, "bad.obj", 32, LOCATION)

    def test_huge_finite_coordinates_normalize_without_overflow(self):
        v, f = load(
            "v -1e308 0 0\nv 1e308 0 0\nv 0 1e308 0\nf 1 2 3", "huge.obj", 32, LOCATION
        )
        self.assertEqual(len(f), 1)
        self.assertTrue(all(sum(map(abs, p)) <= 33 for p in v))

    def test_solid_program_ram_budget_includes_stack_reserve(self):
        source = (
            'int m=load_obj("shape.obj")\nclear()\ndraw_mesh(m)\n'
            + "pixel_on(0,0)\n" * 1000
        )
        with self.assertRaisesRegex(CompileError, "RAM.*stack"):
            compile_source(source, assets={"shape.obj": QUAD}, optimization="reference")
