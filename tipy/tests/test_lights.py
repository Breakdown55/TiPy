import unittest
from tipy import compile_source
from tipy.errors import CompileError

MODEL='int m=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2)])\n'


class LightTests(unittest.TestCase):
    def test_invalid_constant_parameters(self):
        for src in ('ambient_light(-1)','ambient_light(17)','directional_light(0,0,0)',
                    'directional_light(1,0,0,intensity=17)','point_light(513,0,0)',
                    'point_light(0,0,0,radius=0)','point_light(0,0,0,radius=1025)'):
            with self.subTest(source=src),self.assertRaises(CompileError):compile_source(src)

    def test_unused_lights_are_not_linked(self):
        a=compile_source('def unused():\n    point_light(0,0,10)\npixel_on(0,0)')['assembly']
        self.assertNotIn('__gfx_light_',a)

    def test_point_calculations_are_absent_from_directional_only_program(self):
        a=compile_source(MODEL+'no_lights()\ndirectional_light(0,0,1)\ndraw_mesh(m,cull=False)')['assembly']
        self.assertNotIn('__gfx_solid_facing:',a)
        self.assertNotIn('__gfx_wf_draw:',a)
        self.assertNotIn('__gfx_light_point:',a)
        self.assertNotIn('__heap_start:',a)

    def test_light_program_respects_ram_budget(self):
        from pathlib import Path
        result=compile_source((Path(__file__).parents[2]/'examples/lights.tip').read_text())
        self.assertLess(result['estimated_bytes'],24170)
