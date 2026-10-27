import unittest
from tipy.tests.reference import compile_source
from tipy.errors import CompileError


class CameraTests(unittest.TestCase):
    def test_short_directions_keep_normalization_precision(self):
        from tipy.camera_api import camera_basis
        basis = camera_basis([0,0,0,1,1,0,0,0,1])
        self.assertLess(abs(sum(v*v for v in basis[6:9]) - 16384**2), 65536)

    def test_constant_configuration_omits_runtime_camera_setup(self):
        assembly = compile_source('init_camera(fov=60,near=1,far=300)\ncamera(0,0,80,0,0,0)\npoint(10,0,0)')['assembly']
        self.assertIn('__gfx_cam_project:', assembly)
        self.assertNotIn('__gfx_cam_normalize:', assembly)
        self.assertNotIn('__gfx_cam_fov_table:', assembly)
        self.assertNotIn('__obj_f32_multiply:', assembly)

    def test_dynamic_configuration_links_native_setup(self):
        assembly = compile_source('int f=60\nint z=80\ninit_camera(f)\ncamera(0,0,z,0,0,0)\npoint(1,2,3)')['assembly']
        self.assertIn('__gfx_cam_normalize:', assembly)
        self.assertIn('__gfx_cam_fov_table:', assembly)

    def test_unused_camera_is_not_linked(self):
        assembly = compile_source('def unused():\n    init_camera()\n    point(1,2,3)\npixel_on(1,2)')['assembly']
        self.assertNotIn('__gfx_cam_', assembly)
        self.assertNotIn('__heap_start:', assembly)

    def test_invalid_camera_configuration(self):
        for source in ['init_camera(0)', 'set_fov(180)', 'init_camera(60,0,100)',
                       'init_camera(60,20,10)', 'camera(0,0,0,0,0,0)',
                       'camera(0,0,80,0,0,0,0,0,1)', 'init_camera(fov=True)']:
            with self.subTest(source=source), self.assertRaises(CompileError):
                compile_source(source)

    def test_py5_camera_namespace(self):
        assembly = compile_source('import py5 as p\np.init_camera(near=2,far=200)\np.set_fov(75)\np.point(1,2,3)')['assembly']
        self.assertIn('__gfx_cam_project:', assembly)
