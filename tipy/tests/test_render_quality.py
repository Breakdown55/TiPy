import unittest
from tipy import compile_source
from tipy.errors import CompileError


class RenderQualityTests(unittest.TestCase):
    def test_explicit_policy_and_exact_default(self):
        source='int m=mesh([(0,10,0),(-10,-10,0),(10,-10,0)],faces=[(0,1,2)])\ndraw_mesh(m)\nrender()'
        self.assertNotIn('__gfx_quality_',compile_source(source)['assembly'])
        exact=compile_source('set_render_quality("exact")\n'+source)['assembly']
        self.assertNotIn('__gfx_quality_',exact)
        aggressive=compile_source('set_render_quality("aggressive")\n'+source)['assembly']
        self.assertIn('__gfx_quality_quantize:',aggressive)
        self.assertNotIn('__gfx_quality_light_cache:',aggressive)

    def test_invalid_or_dynamic_policy_is_rejected(self):
        for source in ('set_render_quality("typo")','set_render_quality(3)',
                       'str q="aggressive"\nset_render_quality(q)',
                       'set_render_quality("exact")\nset_render_quality("aggressive")',
                       'if True:\n    set_render_quality("aggressive")'):
            with self.subTest(source=source),self.assertRaises(CompileError):
                compile_source(source)

    def test_unused_policy_does_not_link_a_renderer(self):
        self.assertNotIn('__gfx_quality_',compile_source('set_render_quality("aggressive")\nint x=1')['assembly'])
