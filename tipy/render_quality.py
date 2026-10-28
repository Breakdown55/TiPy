"""Explicit compile-time approximation policy for the general mesh renderer."""
from .errors import CompileError


def prepare(tree):
    quality = 'exact'
    found = False
    body = []
    for statement in tree['body']:
        value = statement.get('value') or {}
        if statement.get('kind') == 'expression_statement' and value.get('kind') == 'call' and value.get('name') == 'set_render_quality':
            args = value['arguments']
            if found or len(args) != 1 or args[0].get('kind') != 'string' or args[0]['value'] not in {'exact','aggressive'}:
                raise CompileError('Use one top-level set_render_quality("exact" or "aggressive")', value)
            quality = args[0]['value']
            found = True
        else:
            body.append(statement)
    tree['body'] = body
    tree['render_quality'] = quality


def apply(assembly, quality):
    if quality == 'exact' or '__gfx_mesh_draw:' not in assembly:
        return assembly
    # Nine retained square-root bits and six fractional matrix bits. Inputs
    # still use i16/Q8 storage; this changes only native geometric precision.
    assembly = assembly.replace('ld a,12\n__gfx_mesh_sqrt24_loop:',
                                'ld a,9\n__gfx_mesh_sqrt24_loop:')
    assembly = assembly.replace('ld h,b\n        ld l,c\n        ret',
                                'ld h,b\n        ld l,c\n        add hl,hl\n        add hl,hl\n        add hl,hl\n        ret')
    start = assembly.index('__gfx_mesh_draw:')
    end = assembly.index('__gfx_mesh_draw_return:', start)
    draw = assembly[start:end]
    draw = draw.replace('        ld hl,__gfx_wf_records',
                        '        call __gfx_quality_quantize\n        ld hl,__gfx_wf_records')
    assembly = assembly[:start]+draw+assembly[end:]
    assembly += '''
__gfx_quality_quantize:
        ld hl,__gfx_mesh_view
        ld b,9
__gfx_quality_quantize_loop:
        ld a,(hl)
        add a,2
        ld e,a
        inc hl
        ld d,(hl)
        jr nc,__gfx_quality_quantize_no_carry
        inc d
__gfx_quality_quantize_no_carry:
        ld (hl),d
        dec hl
        ld a,e
        and 252
        ld (hl),a
        inc hl
        inc hl
        djnz __gfx_quality_quantize_loop
        ret
'''
    if '__gfx_light_faces:' in assembly:
        assembly = assembly.replace('__gfx_light_faces:', '__gfx_quality_light_full:')
        assembly += '''
; A tagged direct-mapped cache. Each key includes face, position, scale and
; full rotation, so separate instances never inherit one another's lighting.
; Only light-state changes are allowed to lag by up to three renders.
__gfx_light_faces:
        push ix
        ld ix,0
        add ix,sp
        push iy
        ld e,(ix+4)
        ld d,(ix+5)
        ld a,e
        srl a
        and 15
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld bc,__gfx_quality_light_cache
        add hl,bc
        push hl
        pop iy
        ld a,(__gfx_quality_frame)
        and 3
        jr z,__gfx_quality_light_refresh
        ld a,(iy+0)
        cp e
        jr nz,__gfx_quality_light_refresh
        ld a,(iy+1)
        cp d
        jr nz,__gfx_quality_light_refresh
        push iy
        pop de
        inc de
        inc de
        ld hl,__gfx_mesh_instance
        ld b,8
__gfx_quality_light_instance:
        ld a,(de)
        cp (hl)
        jr nz,__gfx_quality_light_refresh
        inc de
        inc hl
        djnz __gfx_quality_light_instance
        ld hl,__gfx_mesh_rotation
        ld b,18
__gfx_quality_light_rotation:
        ld a,(de)
        cp (hl)
        jr nz,__gfx_quality_light_refresh
        inc de
        inc hl
        djnz __gfx_quality_light_rotation
        ld l,(iy+28)
        ld h,0
        jr __gfx_quality_light_return
__gfx_quality_light_refresh:
        ld l,(ix+4)
        ld h,(ix+5)
        push hl
        call __gfx_quality_light_full
        pop de
        ld (iy+0),e
        ld (iy+1),d
        ld (iy+28),l
        push hl
        push iy
        pop de
        inc de
        inc de
        ld hl,__gfx_mesh_instance
        ld bc,8
        ldir
        ld hl,__gfx_mesh_rotation
        ld bc,18
        ldir
        pop hl
__gfx_quality_light_return:
        pop iy
        ld sp,ix
        pop ix
        ret
__gfx_quality_light_cache: defs 512,0
'''
    assembly = assembly.replace('__gfx_render:', '__gfx_render:\n        call __gfx_quality_next_frame')
    assembly += '''
__gfx_quality_frame: db 0
__gfx_quality_next_frame:
        ld a,(__gfx_quality_frame)
        inc a
        ld (__gfx_quality_frame),a
        ret
'''
    return assembly
