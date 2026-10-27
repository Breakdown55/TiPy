"""Allocation-free indexed models, Q8.8 model/view transforms and clipping.

Angles use 256 steps per turn; size=256 is 1x. Buffers are reused across draws.
The bounded path shares the existing reciprocal projector and edge rasterizer.
"""
import math
from .camera_math import NativeRoutine,invoke
from .list_runtime import add,sub,word


def routines(capacity=64):
    out=['''__gfx_mesh_ready: dw 0
__gfx_mesh_near: dw 512
__gfx_mesh_far: dw 30720
__gfx_mesh_eye: dw 0,0,80
__gfx_mesh_basis: dw 256,0,0,0,256,0,0,0,-256
__gfx_mesh_rotation: defs 18,0
__gfx_mesh_view: defs 18,0
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
    r.expr(invoke('wf_normalize',sub(0,'z'),0,'x','@__gfx_mesh_basis'))
    r.expr(invoke('wf_normalize','x','y','z',add('@__gfx_mesh_basis',12)))
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

    r=NativeRoutine('mesh_vertex_dot',('a','b'),('sum','v'))
    r.set('sum',0)
    for i in range(3):
        r.set('v',invoke('mesh_imul',word(add('b',i*2)),word(add('a',i*2))))
        r.set('sum',add('sum','v'))
    r.ret('sum');out.append(r.finish())

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
    r=NativeRoutine('mesh_mul',('a','b'),('negative','result','v'))
    r.set('negative',0)
    for name in ('a','b'):
        r.if_(('negative',name),lambda name=name:(r.set(name,sub(0,name)),r.set('negative',sub(1,'negative'))))
    r.if_(('ult','a','b'),lambda:(r.set('v','a'),r.set('a','b'),r.set('b','v')))
    done=r.label()
    for coefficient in (0,256,512):
        def shortcut(coefficient=coefficient):
            r.set('result',0 if coefficient==0 else 'a' if coefficient==256 else add('a','a'));r.emit(f'jp {done}')
        r.if_(('eq','b',coefficient),shortcut)
    r.expr('b');r.emit('ld a,h','push af');r.expr('a');r.emit('ld d,h','ld e,l');r.expr('b');r.emit('ld a,l','ld hl,0','ld c,0','ld b,8')
    loop,skip=r.label(),r.label();r.mark(loop)
    r.emit('add hl,hl','rl c','sla a',f'jr nc,{skip}','add hl,de',f'jr nc,{skip}','inc c');r.mark(skip);r.emit(f'djnz {loop}','ld a,l','ld l,h','ld h,c','bit 7,a')
    rounded=r.label();r.emit(f'jr z,{rounded}','inc hl');r.mark(rounded);r.emit('pop af','or a')
    no_high=r.label();r.emit(f'jr z,{no_high}','add hl,de');r.mark(no_high);r._store('result')
    r.mark(done);r.if_(('ne','negative',0),lambda:r.ret(sub(0,'result')));r.ret('result');out.append(r.finish())

    r=NativeRoutine('mesh_draw',('model','x','y','z','yaw','pitch','roll','size'),
                    ('sx','cx','sy','cy','sz','cz','a','b','i','j','p','record','vertices','edges','count','radius'))
    r.if_(('eq',word('@__gfx_mesh_ready'),0),lambda:r.expr(invoke('mesh_init',75,2,120)))
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
            r.write(add('@__gfx_mesh_view',add('i','j')),mul('a','size'));r.set('j',add('j',2))
        r.while_(('ult','j',6),matrix_col);r.set('i',add('i',6))
    r.while_(('ult','i',18),matrix_row)
    r.set('p',add('model',8));r.set('record','@__gfx_wf_records');r.set('count',0)
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
    r.expr(invoke('wf_draw','vertices','edges','p'));r.ret();out.append(r.finish())
    return out
