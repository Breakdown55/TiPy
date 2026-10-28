"""Experimental native quality variants for measured screenshot comparisons.

This does not change the editor's compiler defaults. All approximations execute
on the emulated Z80; the host only assembles, measures and captures the LCD.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tipy import mesh_runtime, solid_runtime, graphics_runtime
from tipy.project import compile_project
from tipy.assembly_size import estimate

PROFILES = [
    dict(name="Exact", root_bits=12, matrix_bits=8, light_stride=1, row_step=1),
    dict(name="Conservative math", root_bits=11, matrix_bits=8, light_stride=1, row_step=1),
    dict(name="Balanced", root_bits=10, matrix_bits=7, light_stride=2, row_step=1),
    dict(name="Aggressive", root_bits=9, matrix_bits=6, light_stride=4, row_step=1),
    dict(name="Maximum", root_bits=8, matrix_bits=5, light_stride=4, row_step=2),
]


def configure(profile):
    original_mesh = mesh_runtime.routines
    original_solid = solid_runtime.routines
    original_graphics = graphics_runtime.graphics_assembly

    def mesh(*args, **kwargs):
        groups = original_mesh(*args, **kwargs)
        if profile['name'] == 'Aggressive':
            return groups
        drop = 12-profile['root_bits']
        if drop:
            groups = [g.replace('ld a,12\n__gfx_mesh_sqrt24_loop:',
                                f"ld a,{12-drop}\n__gfx_mesh_sqrt24_loop:")
                      .replace('ld h,b\n        ld l,c\n        ret',
                               'ld h,b\n        ld l,c\n'+('        add hl,hl\n'*drop)+'        ret')
                      for g in groups]
        drop = 8-profile['matrix_bits']
        if drop:
            groups = [g.replace('        ld hl,__gfx_wf_records',
                                '        call __gfx_preview_quantize\n        ld hl,__gfx_wf_records')
                      if g.startswith('__gfx_mesh_draw:') else g for g in groups]
            groups.append(f'''__gfx_preview_quantize:
        ld hl,__gfx_mesh_view
        ld b,9
__gfx_preview_quantize_loop:
        ld a,(hl)
        add a,{1 << (drop-1)}
        ld e,a
        inc hl
        ld d,(hl)
        jr nc,__gfx_preview_quantize_no_carry
        inc d
__gfx_preview_quantize_no_carry:
        ld (hl),d
        dec hl
        ld a,e
        and {255 ^ ((1 << drop)-1)}
        ld (hl),a
        inc hl
        inc hl
        djnz __gfx_preview_quantize_loop
        ret''')
        return groups

    def solid(*args, **kwargs):
        groups = original_solid(*args, **kwargs)
        if profile['row_step'] == 2:
            groups = [re.sub(r'inc b\n        ld a,b\n        cp (\(ix-\d+\))',
                             r'inc b\n        inc b\n        ld a,b\n        cp \1',g)
                      .replace('ld l,d\n        ld h,0','ld a,d\n        and 254\n        ld l,a\n        ld h,0')
                      .replace('ld l,e\n','ld a,e\n        inc a\n        and 254\n        ld l,a\n')
                      if g.startswith('__gfx_solid_flat_polygon:') else g for g in groups]
            groups = [g.replace('ld a,d\n        and 3', 'ld a,d\n        srl a\n        and 3')
                      if g.startswith('__gfx_solid_span:') else g for g in groups]
        return groups

    def graphics(used):
        assembly = original_graphics(used)
        if profile['name'] == 'Aggressive':
            return assembly  # The normal compiler applies this selected policy.
        stride = profile['light_stride']
        if stride > 1 and '__gfx_light_faces:' in assembly:
            assembly = assembly.replace('__gfx_light_faces:', '__gfx_preview_light_full:')
            assembly += f'''
; Experimental per-face lighting cache: 2/4-frame temporal approximation.
; Pointer tags reject hash collisions; these comparisons use one instance/model.
__gfx_light_faces:
        push ix
        ld ix,0
        add ix,sp
        ld e,(ix+4)
        ld d,(ix+5)
        ld a,e
        srl a
        and 63
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        ld bc,__gfx_preview_light_cache
        add hl,bc
        push hl
        ld a,(__gfx_preview_frame)
        and {stride-1}
        jr z,__gfx_preview_light_refresh
        ld a,(hl)
        cp e
        jr nz,__gfx_preview_light_refresh
        inc hl
        ld a,(hl)
        cp d
        jr nz,__gfx_preview_light_refresh
        inc hl
        ld l,(hl)
        ld h,0
        pop de
        jr __gfx_preview_light_return
__gfx_preview_light_refresh:
        push de
        call __gfx_preview_light_full
        pop de
        pop bc
        ld a,e
        ld (bc),a
        inc bc
        ld a,d
        ld (bc),a
        inc bc
        ld a,l
        ld (bc),a
__gfx_preview_light_return:
        ld sp,ix
        pop ix
        ret
__gfx_preview_light_cache: defs 256,0
'''
        if stride > 1 or profile['row_step'] > 1:
            assembly = assembly.replace('__gfx_render:',
                                        '__gfx_render:\n        call __gfx_preview_resolve')
            assembly += '''
__gfx_preview_frame: db 0
__gfx_preview_resolve:
        ld a,(__gfx_preview_frame)
        inc a
        ld (__gfx_preview_frame),a
'''
            if profile['row_step'] == 2:
                assembly += '''        ld hl,0x9340
        ld de,0x934c
        ld a,32
__gfx_preview_resolve_row:
        push af
        ld b,12
__gfx_preview_resolve_byte:
        ld a,(de)
        or (hl)
        ld (hl),a
        ld (de),a
        inc hl
        inc de
        djnz __gfx_preview_resolve_byte
        pop af
        ld bc,12
        add hl,bc
        ex de,hl
        add hl,bc
        ex de,hl
        dec a
        jr nz,__gfx_preview_resolve_row
'''
            assembly += '        ret\n'
        return assembly

    mesh_runtime.routines = mesh
    solid_runtime.routines = solid
    graphics_runtime.graphics_assembly = graphics


if __name__ == '__main__':
    profile = PROFILES[int(sys.argv[1])]
    configure(profile)
    source=sys.stdin.read()
    if profile['name']=='Aggressive':source='set_render_quality("aggressive")\n'+source
    result = compile_project({'main.tip':source}, 'main.tip')
    result['estimated_bytes'] = estimate(result['assembly'])
    result['quality_profile'] = profile
    print(json.dumps(result))
