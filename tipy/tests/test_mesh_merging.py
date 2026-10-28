import unittest
from tipy import compile_source
from tipy.graphics_library import walk
from tipy.mesh_api import merge_coplanar


class MeshMergingTests(unittest.TestCase):
    def test_only_convex_planar_equally_shaded_pairs_merge(self):
        vertices = [[-10, -10, 0], [10, -10, 0], [10, 10, 0], [-10, 10, 0]]
        records = [([0, 1, 2], 12, [0, 0, 256]), ([0, 2, 3], 12, [0, 0, 256])]
        self.assertEqual(merge_coplanar(vertices, records)[0][0], [0, 1, 2, 3])
        bent = [p[:] for p in vertices]
        bent[3][2] = 1
        concave = [p[:] for p in vertices]
        concave[3] = [0, 0, 0]
        self.assertEqual(len(merge_coplanar(bent, records)), 2)
        self.assertEqual(len(merge_coplanar(concave, records)), 2)
        self.assertEqual(
            len(merge_coplanar(vertices, [records[0], ([0, 2, 3], 11, [0, 0, 256])])), 2
        )

    def test_aggressive_point_lights_use_merged_faces_with_centroids(self):
        model = "int m=mesh([(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)],faces=[(0,1,2),(0,2,3)])\ndraw_mesh(m)"

        def faces(source):
            result = compile_source(source, optimization="reference")
            return next(
                n["face_count"]
                for n in walk(result["checked"])
                if n.get("kind") == "geometry_data"
            )

        self.assertEqual(faces(model), 2)
        self.assertEqual(faces('set_render_quality("aggressive")\n' + model), 1)
        for call in (
            "point_light(0,0,10,intensity=2)",
            "__tipy_native_solid_point_light(0,0,10,80,2)",
        ):
            self.assertEqual(
                faces('set_render_quality("aggressive")\n' + call + "\n" + model), 1
            )
            result = compile_source(
                'set_render_quality("aggressive")\n' + call + "\n" + model,
                optimization="reference",
            )
            packet = next(
                n for n in walk(result["checked"]) if n.get("kind") == "geometry_data"
            )
            self.assertTrue(packet["face_centroids"])
        self.assertEqual(
            faces(
                'set_render_quality("aggressive")\npoint_light(0,0,10,intensity=1)\n'
                + model
            ),
            1,
        )
