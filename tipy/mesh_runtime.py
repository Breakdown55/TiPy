"""Allocation-free indexed models, Q8.8 model/view transforms and clipping.

Angles use 256 steps per turn; size=256 is 1x. Buffers are reused across draws.
The bounded path shares the existing reciprocal projector and edge rasterizer.
"""
import math
from .camera_math import NativeRoutine,invoke
from .list_runtime import add,sub,word


def routines(capacity=64,solid=False,extended=False,wire=True,dynamic_depth=False):
    out=['''__gfx_mesh_ready: dw 0
__gfx_mesh_near: dw 512
__gfx_mesh_far: dw 30720
__gfx_mesh_eye: dw 0,0,80
__gfx_mesh_basis: dw 256,0,0,0,256,0,0,0,-256
__gfx_mesh_rotation: defs 18,0
__gfx_mesh_view: defs 18,0
__gfx_mesh_norm_view: defs 18,0
__gfx_mesh_center: defs 6,0
__gfx_mesh_vertex: defs 6,0
__gfx_mesh_gain: defs 512,0''']
    out.append('__gfx_mesh_sine: dw '+','.join(str(round(math.sin(i*math.tau/256)*256)) for i in range(256)))
    out.append('__gfx_mesh_focals: dw '+','.join(str(round(32/math.tan(math.radians(f)/2)*16)) for f in range(55,121)))
    r=NativeRoutine('mesh_fov',('fov',),('focal','i','p'))
    r.if_(('slt','fov',55),lambda:r.fail(14));r.if_(('slt',120,'fov'),lambda:r.fail(14))
    r.set('focal',word(add('@__gfx_mesh_focals',add(sub('fov',55),sub('fov',55)))))
    r.set('i',256);r.set('p','@__gfx_mesh_gain')
    def gain():
        r.expr('i');r.emit('ld b,h','ld c,l');r.expr('focal')
        r.emit('ld d,0','ld e,h','ld h,l','ld l,0')
        for _ in range(4):r.emit('add hl,hl','rl e','rl d')
        r.emit('call __gfx_wf_udiv')
        r.emit('push hl');r.expr('p');r.emit('ex de,hl','pop hl','ld a,l','ld (de),a','inc de','ld a,h','ld (de),a')
        r.set('p',add('p',2));r.set('i',add('i',1))
    r.while_(('ult','i',512),gain);r.write('@__gfx_mesh_ready',1);r.ret();out.append(r.finish())
    r=NativeRoutine('mesh_init',('fov','near','far'))
    r.if_(('slt','near',1),lambda:r.fail(14));r.if_(('sge','near','far'),lambda:r.fail(14));r.if_(('slt',127,'far'),lambda:r.fail(14))
    for name in ('near','far'):
        r.expr(name);r.emit('ld h,l','ld l,0')
        r.emit(f'ld (__gfx_mesh_{name}),hl')
    r.expr(invoke('mesh_fov','fov'));r.ret();out.append(r.finish())

    r=NativeRoutine('mesh_look',('ex','ey','ez','tx','ty','tz'),('x','y','z','a','b'))
    for axis in 'xyz':
        for prefix in ('e','t'):
            r.if_(('slt',prefix+axis,-120),lambda:r.fail(14));r.if_(('slt',120,prefix+axis),lambda:r.fail(14))
        r.set(axis,sub('t'+axis,'e'+axis));r.write(add('@__gfx_mesh_eye','xyz'.index(axis)*2),'e'+axis)
    r.if_(('eq','x',0),lambda:r.if_(('eq','z',0),lambda:r.fail(14)))
    r.expr(invoke('mesh_normalize',sub(0,'z'),0,'x','@__gfx_mesh_basis'))
    r.expr(invoke('mesh_normalize','x','y','z',add('@__gfx_mesh_basis',12)))
    for axis,(j,k) in enumerate(((1,2),(2,0),(0,1))):
        r.set('a',invoke('wf_qmul',word(add('@__gfx_mesh_basis',j*2)),word(add('@__gfx_mesh_basis',12+k*2))))
        r.set('b',invoke('wf_qmul',word(add('@__gfx_mesh_basis',k*2)),word(add('@__gfx_mesh_basis',12+j*2))))
        r.write(add('@__gfx_mesh_basis',6+axis*2),sub('a','b'))
    r.ret();out.append(r.finish())

    r=NativeRoutine('mesh_dot',('a','b','step'),('sum','v'))
    r.set('sum',0)
    for i in range(3):
        r.set('v',invoke('mesh_mul',word('a'),word('b')))
        r.set('sum',add('sum','v'))
        if i!=2:r.set('a',add('a',2));r.set('b',add('b','step'))
    r.ret('sum');out.append(r.finish())

    r=NativeRoutine('mesh_vertex_dot',('a','b'),('sum',))
    r.set('sum',0)
    for i in range(3):
        r.expr(('byte',add('b',i*2)));r.emit('ld c,l')
        r.expr(word(add('a',i*2)));r.emit('ld d,h','ld e,l','ld a,c','or a')
        positive,ready,loop,skip,done=(r.label() for _ in range(5))
        r.emit(f'jp p,{positive}','neg','ld c,a','xor a','sub e','ld e,a','sbc a,a','sub d','ld d,a','ld a,c',f'jr {ready}')
        r.mark(positive);r.mark(ready);r.emit('ld hl,0','or a',f'jr z,{done}')
        r.mark(loop);r.emit('srl a',f'jr nc,{skip}','add hl,de');r.mark(skip)
        r.emit('sla e','rl d','or a',f'jr nz,{loop}');r.mark(done)
        offset=r.slots['sum'];r.emit(f'ld e,(ix{offset:+d})',f'ld d,(ix{offset+1:+d})','add hl,de');r._store('sum')
    r.emit(f'jp {r.name}_return');out.append(r.finish())

    r=NativeRoutine('mesh_imul',('a','b'))
    r.expr('a');r.emit('ld a,l','or a')
    positive,ready,loop,skip,end=(r.label() for _ in range(5))
    r.emit(f'jp p,{positive}','neg','ld c,a');r.expr('b')
    r.emit('xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a','ld a,c',f'jr {ready}')
    r.mark(positive);r.expr('b');r.mark(ready);r.emit('ld d,h','ld e,l','ld hl,0','or a',f'jr z,{end}')
    r.mark(loop);r.emit('srl a',f'jr nc,{skip}','add hl,de');r.mark(skip)
    r.emit('sla e','rl d','or a',f'jr nz,{loop}');r.mark(end);r.emit(f'jp {r.name}_return');out.append(r.finish())

    # Every model/view coefficient is at most 512. Multiply the low eight
    # coefficient bits in eight iterations, with exact 1x/2x shortcuts.
    r=NativeRoutine('mesh_mul',('a','b'))
    r.expr('a');r.emit('push hl');r.expr('b');r.emit('ex de,hl','pop hl')
    r.emit('ld a,h','xor d','and 128',"ex af,af'",'bit 7,h','jr z,__gfx_mesh_mul_a_positive',
           'xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a')
    r.mark('__gfx_mesh_mul_a_positive')
    r.emit('bit 7,d','jr z,__gfx_mesh_mul_b_positive','xor a','sub e','ld e,a','sbc a,a','sub d','ld d,a')
    r.mark('__gfx_mesh_mul_b_positive')
    r.emit('ld a,h','cp d','jr c,__gfx_mesh_mul_swap','jr nz,__gfx_mesh_mul_ordered','ld a,l','cp e','jr nc,__gfx_mesh_mul_ordered')
    r.mark('__gfx_mesh_mul_swap');r.emit('ex de,hl')
    r.mark('__gfx_mesh_mul_ordered')
    r.emit('ld a,d','or e','jr nz,__gfx_mesh_mul_nonzero','ld hl,0','jr __gfx_mesh_mul_sign')
    r.mark('__gfx_mesh_mul_nonzero')
    r.emit('ld a,e','or a','jr nz,__gfx_mesh_mul_product','ld a,d','cp 1','jr z,__gfx_mesh_mul_sign','cp 2','jr nz,__gfx_mesh_mul_product','add hl,hl','jr __gfx_mesh_mul_sign')
    r.mark('__gfx_mesh_mul_product')
    r.emit('push de','ex de,hl','ld a,l','ld hl,0','ld c,0','ld b,8')
    r.mark('__gfx_mesh_mul_bits')
    r.emit('add hl,hl','rl c','sla a','jr nc,__gfx_mesh_mul_skip','add hl,de','jr nc,__gfx_mesh_mul_skip','inc c')
    r.mark('__gfx_mesh_mul_skip')
    r.emit('djnz __gfx_mesh_mul_bits','ld a,l','ld l,h','ld h,c','bit 7,a','jr z,__gfx_mesh_mul_rounded','inc hl')
    r.mark('__gfx_mesh_mul_rounded')
    r.emit('pop bc','ld a,b','or a','jr z,__gfx_mesh_mul_sign','add hl,de')
    r.mark('__gfx_mesh_mul_sign')
    r.emit("ex af,af'",'or a',f'jp z,{r.name}_return','xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a',f'jp {r.name}_return')
    out.append(r.finish())

    r=NativeRoutine('mesh_draw',('model','x','y','z','yaw','pitch','roll','size','filled','cull','shaded','depth_test','outline'),
                    ('sx','cx','sy','cy','sz','cz','a','b','i','j','p','record','vertices','edges','count','radius'))
    r.if_(('eq',word('@__gfx_mesh_ready'),0),lambda:r.expr(invoke('mesh_init',75,2,120)))
    if dynamic_depth:r.write('@__gfx_solid_depth_enabled','depth_test')
    if extended:
        for i,n in enumerate(('x','y','z','size')):r.write(add('@__gfx_mesh_instance',i*2),n)
        r.write('@__gfx_mesh_source',add('model',10))
    r.if_(('ne',word('model'),0x4d54),lambda:r.fail(14))
    r.set('vertices',word(add('model',2)));r.set('edges',word(add('model',4)))
    r.if_(('eq','vertices',0),lambda:r.fail(14));r.if_(('uge','vertices',min(capacity,64)+1),lambda:r.fail(14))
    r.if_(('eq','edges',0),lambda:r.fail(14));r.if_(('uge','edges',193),lambda:r.fail(14))
    r.if_(('slt','size',1),lambda:r.fail(14));r.if_(('slt',512,'size'),lambda:r.fail(14))
    # Reject distant instances before rotating or projecting a single vertex.
    for i,axis in enumerate('xyz'):
        r.set(axis,sub(axis,word(add('@__gfx_mesh_eye',i*2))))
        r.if_(('slt',axis,-120),lambda:r.ret());r.if_(('slt',120,axis),lambda:r.ret())
        r.expr(axis);r.emit(f'ld (__gfx_mesh_vertex+{i*2}),hl')
    # A conservative camera-relative L1 bound guarantees safe Q8 dot products.
    r.set('a',0)
    for axis in 'xyz':
        r.set('b',axis);r.if_(('negative','b'),lambda:r.set('b',sub(0,'b')));r.set('a',add('a','b'))
    r.if_(('slt',120,'a'),lambda:r.ret())
    r.set('i',0)
    def center():
        r.write(add('@__gfx_mesh_center','i'),invoke('mesh_vertex_dot',add('@__gfx_mesh_basis',add('i',add('i','i'))),'@__gfx_mesh_vertex'))
        r.set('i',add('i',2))
    r.while_(('ult','i',6),center)
    r.set('radius',invoke('mesh_imul',word(add('model',6)),'size'))
    r.set('a',word(add('@__gfx_mesh_center',4)))
    r.if_(('slt','a',word('@__gfx_mesh_near')),lambda:r.if_(('ult','radius',sub(word('@__gfx_mesh_near'),'a')),lambda:r.ret()))
    r.if_(('slt',word('@__gfx_mesh_far'),'a'),lambda:r.if_(('ult','radius',sub('a',word('@__gfx_mesh_far'))),lambda:r.ret()))
    general,transformed=r.label(),r.label()
    # Unrotated unit-scale instances can reuse the camera matrix exactly.
    # This includes the common static level + translated pickup case.
    r.expr('yaw');r.emit('ld b,l');r.expr('pitch');r.emit('ld a,l','or b','ld b,a');r.expr('roll');r.emit('ld a,l','or b',f'jp nz,{general}')
    r.if_(('ne','size',256),lambda:r.emit(f'jp {general}'))
    r.write('@__gfx_mesh_identity_active',1)
    for source,target in (('__gfx_mesh_identity','__gfx_mesh_rotation'),('__gfx_mesh_basis','__gfx_mesh_view'))+((('__gfx_mesh_basis','__gfx_mesh_norm_view'),) if solid else ()):
        r.emit(f'ld hl,{source}',f'ld de,{target}','ld bc,18','ldir')
    r.emit(f'jp {transformed}');r.mark(general);r.write('@__gfx_mesh_identity_active',0)
    for angle,axis in (('pitch','x'),('yaw','y'),('roll','z')):
        r.expr(angle);r.emit('ld h,0','add hl,hl','ld de,__gfx_mesh_sine','add hl,de','ld e,(hl)','inc hl','ld d,(hl)','ex de,hl');r._store('s'+axis)
        r.expr(angle);r.emit('ld a,l','add a,64','ld l,a','ld h,0','add hl,hl','ld de,__gfx_mesh_sine','add hl,de','ld e,(hl)','inc hl','ld d,(hl)','ex de,hl');r._store('c'+axis)
    def mul(a,b):return invoke('mesh_mul',a,b)
    coefficients=[mul('cz','cy'),sub(mul(mul('cz','sy'),'sx'),mul('sz','cx')),add(mul(mul('cz','sy'),'cx'),mul('sz','sx')),
                  mul('sz','cy'),add(mul(mul('sz','sy'),'sx'),mul('cz','cx')),sub(mul(mul('sz','sy'),'cx'),mul('cz','sx')),
                  sub(0,'sy'),mul('cy','sx'),mul('cy','cx')]
    for i,value in enumerate(coefficients):r.write(add('@__gfx_mesh_rotation',i*2),value)
    r.set('i',0)
    def matrix_row():
        r.set('j',0)
        def matrix_col():
            r.set('a',invoke('mesh_dot',add('@__gfx_mesh_basis','i'),add('@__gfx_mesh_rotation','j'),6))
            # Unit basis rounding may exceed one by a few Q8 increments.
            r.if_(('slt',256,'a'),lambda:r.set('a',256));r.if_(('slt','a',-256),lambda:r.set('a',-256))
            if solid:r.write(add('@__gfx_mesh_norm_view',add('i','j')),'a')
            r.write(add('@__gfx_mesh_view',add('i','j')),mul('a','size'));r.set('j',add('j',2))
        r.while_(('ult','j',6),matrix_col);r.set('i',add('i',6))
    r.while_(('ult','i',18),matrix_row)
    r.mark(transformed)
    r.set('p',add('model',10));r.set('record','@__gfx_wf_records');r.set('count',0)
    def vertex():
        for i in range(3):
            r.expr(word(add('p',i*2)));r.emit(f'ld (__gfx_mesh_vertex+{i*2}),hl')
        r.set('i',0)
        def axis():
            r.set('a',invoke('mesh_vertex_dot',add('@__gfx_mesh_view',add('i',add('i','i'))),'@__gfx_mesh_vertex'))
            r.expr(word(add('@__gfx_mesh_center','i')));r.emit('push hl');r.expr('a');r.emit('pop de','or a','adc hl,de',f'jp pe,{r.name}_return');r._store('a')
            r.write(add('record','i'),'a');r.set('i',add('i',2))
        r.while_(('ult','i',6),axis);r.set('p',add('p',6));r.set('record',add('record',12));r.set('count',add('count',1))
    r.while_(('ult','count','vertices'),vertex)
    r.write('@__gfx_wf_near',word('@__gfx_mesh_near'));r.write('@__gfx_wf_far',word('@__gfx_mesh_far'));r.write('@__gfx_wf_gain','@__gfx_mesh_gain')
    r.emit('ld a,1','ld (__gfx_ink),a')
    if solid:
        r.if_(('ne','filled',0),lambda:r.if_(('ne',word(add('model',8)),0),lambda:(r.expr(invoke('solid_prepare','vertices')),r.expr(invoke('solid_draw',add('p',add('edges','edges')),word(add('model',8)),'cull','shaded')),r.if_(('eq','outline',0),lambda:r.ret()))))
    if wire:r.expr(invoke('wf_draw','vertices','edges','p'))
    r.ret();out.append(r.finish())
    out.append('__gfx_mesh_identity: dw 256,0,0,0,256,0,0,0,256\n__gfx_mesh_identity_active: dw 0')
    if extended:out.append('__gfx_mesh_instance: defs 8,0\n__gfx_mesh_source: dw 0')
    out.append(_normalize())
    return out


def _normalize():
    """Bounded unit vectors use an allocation-free 24-bit restoring square root."""
    from .shape_runtime import words
    r=NativeRoutine('mesh_normalize',('x','y','z','out'),('sx','sy','sz','max','lo','v','shift')+words('sum',2)+words('p',2))
    r.set('max',0);r.set('shift',0)
    for n in 'xyz':
        r.set('s'+n,0);r.if_(('negative',n),lambda n=n:(r.set(n,sub(0,n)),r.set('s'+n,1)))
        r.if_(('slt','max',n),lambda n=n:r.set('max',n))
    r.if_(('eq','max',0),lambda:r.fail(14));r.if_(('slt',2047,'max'),lambda:r.fail(14))
    def enlarge():
        for n in 'xyz':r.set(n,add(n,n))
        r.set('max',add('max','max'))
        r.set('shift',add('shift',1))
    r.while_(('ult','max',1024),enlarge);r.zero('sum')
    for n in 'xyz':
        r.expr(n);r.emit('ld d,h','ld e,l','ld b,h','ld c,l','call __gfx_wf_umul');r._store('p0');r.emit('ex de,hl');r._store('p1');r.binary('sum','sum','p')
    r.expr('sum0');r.emit('push hl');r.expr('sum1');r.emit('ex de,hl','pop hl','call __gfx_mesh_sqrt24');r._store('lo')
    for i,n in enumerate('xyz'):
        r.expr(n);r.emit('ld d,0','ld e,h','ld h,l','ld l,0','push hl');r.expr('lo');r.emit('srl h','rr l','ld b,h','ld c,l','pop hl','add hl,bc')
        carry=r.label();r.emit(f'jr nc,{carry}','inc de');r.mark(carry);r.emit('push hl');r.expr('lo');r.emit('ld b,h','ld c,l','pop hl','call __gfx_wf_udiv');r._store('v')
        r.if_(('ne','s'+n,0),lambda:r.set('v',sub(0,'v')));r.write(add('out',i*2),'v')
    r.expr('shift');r.emit('ld b,l');r.expr('lo');r.emit('inc b')
    loop,ready=r.label(),r.label();r.emit(f'jr {ready}');r.mark(loop);r.emit('srl h','rr l');r.mark(ready);r.emit(f'djnz {loop}',f'jp {r.name}_return')
    return r.finish()+'''
; Exact restoring square root for the bounded 24-bit squared vector length.
; DE:HL input (< 3*2048^2), HL output. No multiply or divide in the loop.
; Main/alternate BC, DE, HL are scratch; IX/IY are preserved.
__gfx_mesh_sqrt24:
        ld d,e
        ld e,h
        ld h,l
        ld l,0
        exx
        ld hl,0
        ld bc,0
        ld a,12
__gfx_mesh_sqrt24_loop:
        exx
        add hl,hl
        rl e
        rl d
        exx
        adc hl,hl
        exx
        add hl,hl
        rl e
        rl d
        exx
        adc hl,hl
        sla c
        rl b
        ld d,b
        ld e,c
        sla e
        rl d
        inc de
        or a
        sbc hl,de
        jr nc,__gfx_mesh_sqrt24_accept
        add hl,de
        jr __gfx_mesh_sqrt24_next
__gfx_mesh_sqrt24_accept:
        inc bc
__gfx_mesh_sqrt24_next:
        dec a
        jr nz,__gfx_mesh_sqrt24_loop
        ld h,b
        ld l,c
        ret'''
