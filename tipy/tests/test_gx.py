import unittest
from tipy import compile_source
from tipy.errors import CompileError
from tipy.toolkit_namespace import ALIASES


class GxTests(unittest.TestCase):
    def test_namespace_boundary(self):
        self.assertTrue({'begin', 'vertex', 'end', 'draw', 'depth_test', 'cull', 'shade', 'load_matrix', 'mesh', 'draw_mesh'} <= ALIASES['gx'].keys())
        self.assertTrue({'draw_rect', 'draw_text', 'load_obj', 'draw_model', 'cube', 'plane', 'key_down'} <= ALIASES['egx'].keys())
        self.assertFalse({'draw_rect', 'print', 'input', 'load_obj', 'cube', 'plane', 'camera', 'present', 'translate', 'rotate'} & ALIASES['gx'].keys())

    def test_easy_shapes_do_not_expose_raw_meshes(self):
        for name in ('mesh','draw_mesh'):
            self.assertNotIn(name, ALIASES['egx'])
            with self.assertRaises(CompileError): compile_source('import egx\negx.'+name+'()')

    def test_easy_shapes_pack_geometry_only_when_used(self):
        for shape in ('cube(12)', 'plane(36,28)'):
            r=compile_source('import egx\negx.'+shape+'\negx.render()')
            self.assertNotIn('__heap_start:',r['assembly'])
            self.assertIn('__gfx_mesh_draw:',r['assembly'])
            unused=compile_source('import egx\ndef unused():\n    egx.'+shape+'\npass')
            self.assertNotIn('__gfx_mesh_draw:',unused['assembly'])
            self.assertEqual(unused['estimated_bytes'],1)

    def test_easy_shapes_validate_arguments(self):
        for call in ('cube(41)', 'cube(True)', 'plane(100,100)', 'plane(0,12)', 'plane(12,12,missing=True)'):
            with self.subTest(call=call),self.assertRaises(CompileError):compile_source('import egx\negx.'+call)
        with self.assertRaisesRegex(CompileError,'do not return'):
            compile_source('import egx\nint model=egx.cube()')
        with self.assertRaisesRegex(CompileError,'constant dimension'):
            compile_source('import egx\nint width=20\negx.plane(width,12)')

    def test_reusable_shapes_are_optional_and_keep_functional_shapes(self):
        for source in ('import egx\ncube=egx.Cube()\ncube.rotation_y+=0.03\ncube.draw()\negx.cube(6)',
                       'from egx import Plane as Ground\nfloor=Ground(36,28,y=-10)\nfloor.scale=2\nfloor.draw()',
                       'import egx\nint side=12\nint x=3\ncube=egx.Cube(size=side,x=x)\ncube.draw()'):
            r=compile_source(source)
            self.assertNotIn('__heap_start:',r['assembly'])
        unused=compile_source('import egx\ndef unused():\n    cube=egx.Cube()\n    cube.draw()\npass')
        self.assertNotIn('__gfx_mesh_draw:',unused['assembly'])

    def test_reusable_shape_fields_are_checked(self):
        for source in ('cube=egx.Cube()\ncube.visible=1', 'cube=egx.Cube()\ncube.rotation_y=True',
                       'floor=egx.Plane()\nfloor.width=12','cube=egx.Cube()\ncube.brightness=3',
                       'cube=egx.Cube()\ncube.unknown=3','cube=egx.Cube(size=True)'):
            with self.subTest(source=source),self.assertRaises(CompileError):compile_source('import egx\n'+source)

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
