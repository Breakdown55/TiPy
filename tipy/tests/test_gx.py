import unittest
from tipy import compile_source
from tipy.errors import CompileError
from tipy.toolkit_namespace import ALIASES


class GxTests(unittest.TestCase):
    def test_namespace_boundary(self):
        self.assertTrue({'begin', 'vertex', 'end', 'draw', 'depth_test', 'cull', 'shade', 'load_matrix'} <= ALIASES['gx'].keys())
        self.assertTrue({'draw_rect', 'draw_text', 'load_obj', 'draw_mesh', 'key_down'} <= ALIASES['egx'].keys())
        self.assertFalse({'draw_rect', 'print', 'input', 'load_obj', 'mesh', 'camera', 'present', 'translate', 'rotate'} & ALIASES['gx'].keys())

    def test_literal_indexed_draw_is_heap_free(self):
        r = compile_source('import gx\nfrom gx import TRIANGLES\ngx.clear()\ngx.draw(TRIANGLES,[(2,2),(12,2),(12,12),(2,12)],[0,1,2,0,2,3])\ngx.render()')
        self.assertIn('__gfx_gx_vertex2:', r['assembly'])
        self.assertNotIn('__heap_start:', r['assembly'])
        self.assertNotIn('__gfx_solid_depth: defs', r['assembly'])
        self.assertLess(r['estimated_bytes'], 12000)

    def test_dynamic_indexed_draw_and_matrix_compile(self):
        compile_source('import gx\nvertices=[(2,2),(12,2),(2,12)]\nindices=[0,1,2]\ngx.load_matrix(1,0,0,3,0,1,0,4,0,0,1,0,0,0,0,1)\ngx.draw(gx.TRIANGLES,vertices,indices)\ngx.render()')

    def test_depth_storage_is_demand_linked(self):
        base = 'import gx\n{state}\ngx.begin(gx.TRIANGLES)\ngx.vertex(-3,-3,0)\ngx.vertex(3,-3,0)\ngx.vertex(0,3,0)\ngx.end()\ngx.render()'
        self.assertNotIn('__gfx_solid_depth: defs', compile_source(base.format(state=''))['assembly'])
        self.assertIn('__gfx_solid_depth: defs', compile_source(base.format(state='gx.depth_test(True)'))['assembly'])
        matrix = compile_source('import gx\ngx.load_matrix(1,0,0,3,0,1,0,4,0,0,1,0,0,0,0,1)\ngx.draw(gx.LINES,[(0,0),(1,1)])')
        self.assertNotIn('__heap_start:', matrix['assembly'])

    def test_bad_buffers_are_rejected(self):
        for call in ['gx.draw(gx.TRIANGLES,[(0,0),(1,0)])',
                     'gx.draw(gx.TRIANGLES,[(0,0),(1,0),(0,1)],[0,1,9])',
                     'gx.vertex(1)', 'gx.load_matrix(1,2)',
                     'gx.draw(gx.LINES,[(0,0),(1,1)],dimensions=4)']:
            with self.subTest(call=call), self.assertRaises(CompileError):
                compile_source('import gx\n' + call)
