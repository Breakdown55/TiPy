import unittest
from tipy import compile_source
from tipy.errors import CompileError

MODEL='int m=mesh([(0,10,0),(-10,-10,0),(10,-10,0)],[(0,1),(1,2),(2,0)])\n'


class MeshTests(unittest.TestCase):
    def test_static_models_do_not_allocate_lists_or_general_camera(self):
        r=compile_source(MODEL+'init_camera()\ndraw_mesh(m,yaw=32,size=384)\nrender()')
        self.assertLess(r['estimated_bytes'],10000)
        for unused in ('__heap_start:', '__arr_new:', '__gfx_cam_project:', '__gfx_mat_rotate:'):
            self.assertNotIn(unused,r['assembly'])

    def test_unused_mesh_runtime_is_not_linked(self):
        a=compile_source('def unused():\n    '+MODEL.replace('\n','\n    ')+'draw_mesh(m)\npixel_on(1,2)')['assembly']
        self.assertNotIn('__gfx_mesh_',a)

    def test_reject_invalid_static_geometry(self):
        for args in ('[],[]','[(0,0,0)],[(0,1)]','[(61,0,0)],[(0,0)]',
                     '[(1,2)],[(0,0)]','[(1,2,3)],[(True,0)]','[(1,2,3)],[(0,-1)]'):
            with self.subTest(args=args),self.assertRaises(CompileError):compile_source('int m=mesh('+args+')\ndraw_mesh(m)')

    def test_configuration_and_mixed_xyz_errors(self):
        for s in ('init_camera(20)','init_camera(75,2,128)','init_camera(75,9,3)',
                  'draw_mesh(m,size=0)','draw_mesh(m,size=513)','line(0,0,0,1,1,1)'):
            with self.subTest(source=s),self.assertRaises(CompileError):compile_source(MODEL+'draw_mesh(m)\n'+s)

    def test_unused_mesh_does_not_change_the_live_general_camera(self):
        a=compile_source('def unused():\n    '+MODEL.replace('\n','\n    ')+'draw_mesh(m)\ninit_camera()\npoint(0,0,0)')['assembly']
        self.assertIn('__gfx_cam_project:',a)
        self.assertNotIn('__gfx_mesh_',a)
