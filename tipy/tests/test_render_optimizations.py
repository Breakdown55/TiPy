import unittest
from unittest.mock import patch

from tipy.lexer import tokenize
from tipy.parser import parse
from tipy.checker import check
from tipy.optimization_pipeline import specialize_finite_functions
from tipy.drawing_optimizer import annotate_blank_meshes
from tipy.project import compile_project
from tipy.assembly_size import estimate
from tipy.wireframe import box_surface_bounds


class RenderOptimizationTests(unittest.TestCase):
    def checked(self, source):
        return check(parse(tokenize(source)))[0]

    def test_finite_function_tables_cover_period_and_signed_values(self):
        tree = self.checked("def wave(int a) -> int:\n    a = a & 63\n    if a >= 32:\n        return -(a-32)*(a-32)\n    return a*a\n")
        self.assertEqual(specialize_finite_functions(tree), 1)
        table = tree['body'][0]['finite_table']
        self.assertEqual(table['mask'], 63)
        for value in range(-32768, 32768):
            a = value & 63
            expected = -(a-32)**2 if a >= 32 else a*a
            self.assertEqual(table['values'][a], expected)

    def test_partial_domain_and_drawing_functions_are_not_tabled(self):
        bodies = [
            '    a = a & 15\n    return 10 // (a-3)\n',
            '    a = a & 15\n    pixel_on(a,0)\n    return a\n',
            '    return (a*a) & 15\n',
        ]
        for body in bodies:
            tree = self.checked('def f(int a) -> int:\n'+body)
            self.assertEqual(specialize_finite_functions(tree), 0)

    def test_blank_frame_proof_respects_branches_calls_and_text(self):
        def draw():
            args = [{'kind':'number','value':0} for _ in range(13)]
            args[12] = {'kind':'boolean','value':True}
            return {'kind':'native_graphics','name':'mesh_draw','arguments':args}
        clear = {'kind':'graphics','name':'clear','arguments':[]}
        a,b,c,d = [draw() for _ in range(4)]
        tree = {'body':[clear,{'kind':'if','condition':{'kind':'boolean','value':True},'body':[a],
            'alternative':[{'kind':'text_print','arguments':[]},b]},c,clear,
            {'kind':'call','symbol':'unknown','arguments':[]},d]}
        annotate_blank_meshes(tree)
        self.assertEqual([n['blank_framebuffer'] for n in (a,b,c,d)],[True,False,False,False])

    def test_overlapping_or_duplicate_box_patches_are_rejected(self):
        vertices = [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]
        normal = [0,0,256]
        edges = [(0,1),(1,2),(2,0),(0,2),(2,3),(3,0)]
        records = [([0,1,2],16,normal),([0,2,3],16,normal)]
        self.assertIsNotNone(box_surface_bounds(vertices,records,edges))
        self.assertIsNone(box_surface_bounds(vertices,[records[0],records[0]],edges))
        # Distinct indices at an identical geometric corner are still overlap.
        vertices.append(vertices[1]);edges += [(0,8),(8,2)]
        self.assertIsNone(box_surface_bounds(vertices,[records[0],([0,8,2],16,normal)],edges))

    def test_ram_budget_falls_back_to_compact_native_kernels(self):
        source = 'int m=mesh([(-4,-4,0),(4,-4,0),(0,4,0)],faces=[(0,1,2)])\nclear()\ndraw_mesh(m,cull=False,depth_test=False)\nrender()'
        def budget(source):
            if '__program_end:' in source and ('__gfx_mesh_vertex_plan:' in source or '__gfx_solid_fast_span_tables:' in source):
                return 25000
            return estimate(source)
        with patch('tipy.assembly_size.estimate',side_effect=budget):
            result=compile_project({'main.tip':source},'main.tip')
        self.assertNotIn('__gfx_mesh_vertex_plan:',result['assembly'])
        self.assertNotIn('__gfx_solid_fast_span_tables:',result['assembly'])
        self.assertIn('__gfx_solid_fast_span:',result['assembly'])


if __name__ == '__main__':
    unittest.main()
