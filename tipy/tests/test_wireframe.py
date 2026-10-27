import unittest
from tipy import compile_source


SCENE='no_fill()\ninit_camera(75,2,300)\ncamera(0,0,0,0,0,-1024)\nline(-60,-30,-80,60,-30,-80)\nline(60,-30,-80,60,30,-80)\nrender()'


class WireframeTests(unittest.TestCase):
    def test_aggressive_bounded_packet_uses_compact_pipeline(self):
        a=compile_source(SCENE)['assembly']
        self.assertIn('__gfx_cam_fast_batch:',a)
        self.assertIn('__gfx_cam_fast_look:',a)
        self.assertNotIn('__gfx_cam_normalize:',a)
        self.assertNotIn('__gfx_cam_project:',a)
        self.assertIn('__gfx_wf_octant_111:',a)

    def test_reference_keeps_exact_shared_vertex_pipeline(self):
        a=compile_source(SCENE,optimization='reference')['assembly']
        self.assertIn('__gfx_cam_batch:',a)
        self.assertNotIn('__gfx_cam_fast_batch:',a)

    def test_unsupported_cases_keep_the_general_renderer(self):
        cases=[SCENE.replace('-60','-128'),SCENE.replace('75,2,300','30,2,300'),
               SCENE.replace('camera(0,0,0','camera(1,0,0'),
               SCENE.replace('\ncamera(', '\ntranslate(1,2)\ncamera(',1),
               SCENE.replace('\ncamera(', '\nstroke_weight(2)\ncamera(',1),
               SCENE+'\npoint(1,2,3)',SCENE.replace('camera(0,0,0,0,0,-1024)\n','')]
        for source in cases:
            with self.subTest(source=source):self.assertNotIn('__gfx_cam_fast_batch:',compile_source(source)['assembly'])

    def test_render_and_side_effects_are_batch_barriers(self):
        for barrier in ['render()','stroke(WHITE)','pixel_on(1,2)']:
            source=SCENE.replace('\nline(60,','\n'+barrier+'\nline(60,')
            self.assertNotIn('__gfx_cam_batch:',compile_source(source,optimization='reference')['assembly'])
