"""Bounded Q8.8 wireframes: shared vertices, reciprocal projection, 16-bit clips.

Selected only for compiler-proven small, origin-camera, unit-width packets.
The reference path retains the general Q18.14 renderer. No host drawing occurs.
"""
from .camera_math import NativeRoutine,invoke
from .shape_runtime import slot,words
from .list_runtime import add,sub,word,byte


def routines(capacity):
    out=['__gfx_wf_records: defs '+str(capacity*12)+',0\n__gfx_wf_basis: defs 18,0\n__gfx_wf_near: dw 0\n__gfx_wf_far: dw 0\n__gfx_wf_gain: dw 0']
    import math
    table=[round(128*4096/math.sqrt(n)) for n in range(256,1024)]
    out.append('__gfx_wf_root_table:\n        dw '+','.join(map(str,table)))
    out.append('''__gfx_wf_umul:
        ld hl,0
        ld a,16
__gfx_wf_umul_loop:
        add hl,hl
        rl e
        rl d
        jr nc,__gfx_wf_umul_skip
        add hl,bc
        jr nc,__gfx_wf_umul_skip
        inc de
__gfx_wf_umul_skip:
        dec a
        jr nz,__gfx_wf_umul_loop
        ret
__gfx_wf_udiv:
        ld a,16
__gfx_wf_udiv_loop:
        add hl,hl
        rl e
        rl d
        jr c,__gfx_wf_udiv_carry
        ex de,hl
        or a
        sbc hl,bc
        ex de,hl
        jr nc,__gfx_wf_udiv_bit
        ex de,hl
        add hl,bc
        ex de,hl
        jr __gfx_wf_udiv_next
__gfx_wf_udiv_carry:
        ex de,hl
        or a
        sbc hl,bc
        ex de,hl
__gfx_wf_udiv_bit:
        inc l
__gfx_wf_udiv_next:
        dec a
        jr nz,__gfx_wf_udiv_loop
        ret''')
    r=NativeRoutine('wf_smul',('a','b'),('negative',))
    r.set('negative',0)
    for value in ('a','b'):
        r.if_(('negative',value),lambda value=value:(r.set(value,sub(0,value)),r.set('negative',sub(1,'negative'))))
    shifted,finish=r.label(),r.label()
    r.if_(('eq','b',256),lambda:(r.expr('a'),r.emit('ld h,l','ld l,0',f'jp {finish}')))
    r.expr('a');r.emit('ld e,l','ld d,0');r.expr('b');r.emit('ld a,l','ld hl,0')
    loop,skip,end=r.label(),r.label(),r.label();r.emit('or a',f'jr z,{end}');r.mark(loop)
    r.emit('srl a',f'jr nc,{skip}','add hl,de');r.mark(skip);r.emit('sla e','rl d','or a',f'jr nz,{loop}');r.mark(end)
    r.mark(finish);r.emit('push hl');r.expr('negative');r.emit('ld a,h','or l','pop hl')
    positive=r.label();r.emit(f'jr z,{positive}','xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a');r.mark(positive);r.emit(f'jp {r.name}_return');out.append(r.finish())

    r=NativeRoutine('wf_qmul',('a','b'),('negative','result'))
    r.set('negative',0)
    for name in ('a','b'):
        r.if_(('negative',name),lambda name=name:(r.set(name,sub(0,name)),r.set('negative',sub(1,'negative'))))
    r.expr('a');r.emit('ex de,hl');r.expr('b');r.emit('ld b,h','ld c,l','call __gfx_wf_umul','ld bc,128','add hl,bc')
    carry=r.label();r.emit(f'jr nc,{carry}','inc de');r.mark(carry);r.emit('ld l,h','ld h,e');r._store('result')
    r.if_(('ne','negative',0),lambda:r.ret(sub(0,'result')));r.ret('result');out.append(r.finish())

    r=NativeRoutine('wf_normalize',('x','y','z','out'),words('sum',2)+words('p',2)+('scale','gain','negative','value','result'))
    rescale,again,scaled=r.label(),r.label(),r.label();r.mark(again)
    for name in ('x','y','z'):
        r.if_(('slt',name,-2047),lambda:r.emit(f'jp {rescale}'));r.if_(('slt',2047,name),lambda:r.emit(f'jp {rescale}'))
    r.emit(f'jp {scaled}');r.mark(rescale)
    for name in ('x','y','z'):r.expr(name);r.emit('sra h','rr l');r._store(name)
    r.emit(f'jp {again}');r.mark(scaled)
    r.zero('sum');r.set('scale',0)
    for name in ('x','y','z'):
        r.set('value',name);r.if_(('negative','value'),lambda:r.set('value',sub(0,'value')))
        r.expr('value');r.emit('ld d,h','ld e,l','ld b,h','ld c,l','call __gfx_wf_umul');r._store('p0');r.emit('ex de,hl');r._store('p1');r.binary('sum','sum','p')
    r.if_(('eq','sum0',0),lambda:r.if_(('eq','sum1',0),lambda:r.fail(14)))
    big,reduce,ready=r.label(),r.label(),r.label();r.mark(big)
    r.if_(('ne','sum1',0),lambda:r.emit(f'jp {reduce}'));r.if_(('ult','sum0',1024),lambda:r.emit(f'jp {ready}'))
    r.mark(reduce);r.shift('sum');r.shift('sum');r.set('scale',add('scale',1));r.emit(f'jp {big}');r.mark(ready)
    r.while_(('ult','sum0',256),lambda:(r.shift('sum',False),r.shift('sum',False),r.set('scale',sub('scale',1))))
    r.set('gain',word(add('@__gfx_wf_root_table',add(sub('sum0',256),sub('sum0',256)))))
    def shrink():r.expr('gain');r.emit('srl h','rr l');r._store('gain');r.set('scale',sub('scale',1))
    r.while_(('slt',0,'scale'),shrink)
    def scale_small():
        for name in ('x','y','z'):r.set(name,add(name,name))
        r.set('scale',add('scale',1))
    r.while_(('negative','scale'),scale_small)
    for i,name in enumerate(('x','y','z')):
        r.set('value',name);r.set('negative',0);r.if_(('negative','value'),lambda:(r.set('value',sub(0,'value')),r.set('negative',1)))
        r.expr('value');r.emit('ex de,hl');r.expr('gain');r.emit('ld b,h','ld c,l','call __gfx_wf_umul')
        # Q11 reciprocal gain produces a signed Q8.8 unit vector.
        r.emit('ld bc,1024','add hl,bc');carry=r.label();r.emit(f'jr nc,{carry}','inc de');r.mark(carry)
        for _ in range(5):r.emit('add hl,hl','rl e','rl d')
        r.emit('ex de,hl');r._store('result')
        r.if_(('uge','result',257),lambda:r.set('result',256))
        r.if_(('ne','negative',0),lambda:r.set('result',sub(0,'result')))
        r.write(add('out',i*2),'result')
    r.ret();out.append(r.finish())

    r=NativeRoutine('cam_fast_look',('ex','ey','ez','tx','ty','tz','ux','uy','uz'),('a','b'))
    r.if_(('ne',word('@__gfx_cam_path_mode'),0),lambda:r.fail(14))
    r.expr(invoke('wf_normalize',sub(0,'tz'),0,'tx','@__gfx_wf_basis'))
    r.expr(invoke('wf_normalize','tx','ty','tz',add('@__gfx_wf_basis',12)))
    # up = cross(right, forward), with rounded Q8 products.
    for axis,(j,k) in enumerate(((1,2),(2,0),(0,1))):
        r.set('a',invoke('wf_qmul',word(add('@__gfx_wf_basis',j*2)),word(add('@__gfx_wf_basis',12+k*2))))
        r.set('b',invoke('wf_qmul',word(add('@__gfx_wf_basis',k*2)),word(add('@__gfx_wf_basis',12+j*2))))
        r.expr(sub('a','b'))
        r._store('a');r.if_(('slt',256,'a'),lambda:r.set('a',256));r.if_(('slt','a',-256),lambda:r.set('a',-256));r.write(add('@__gfx_wf_basis',6+axis*2),'a')
    r.ret();out.append(r.finish())

    out.append(_project())

    r=NativeRoutine('wf_lerp',('a','b','n','d'),('negative',))
    r.set('negative',0);r.if_(('slt','b','a'),lambda:(r.set('negative',1),r.set('b',sub('a','b'))),lambda:r.set('b',sub('b','a')))
    r.expr('b');r.emit('ex de,hl');r.expr('n');r.emit('ld b,h','ld c,l','call __gfx_wf_umul')
    r.emit('push hl');r.expr('d');r.emit('ld b,h','ld c,l','pop hl','call __gfx_wf_udiv');r._store('b')
    r.if_(('ne','negative',0),lambda:r.ret(sub('a','b')));r.ret(add('a','b'));out.append(r.finish())

    r=NativeRoutine('wf_inside',('point','axis','bound','keep'))
    r.if_(('slt',word(add('point','axis')),'bound'),lambda:r.ret(sub(1,'keep')))
    r.if_(('eq',word(add('point','axis')),'bound'),lambda:r.ret(1));r.ret('keep');out.append(r.finish())

    r=NativeRoutine('wf_clip',('a','b','axis','bound','keep'),('ia','ib','outside','inside','n','d','x','y','other'))
    for value in ('a','b'):r.set('i'+value,invoke('wf_inside',value,'axis','bound','keep'))
    r.if_(('eq',add('ia','ib'),0),lambda:r.ret(0));r.if_(('eq',add('ia','ib'),2),lambda:r.ret(1))
    r.set('outside','a');r.set('inside','b');r.if_(('ne','ia',0),lambda:(r.set('outside','b'),r.set('inside','a')))
    r.set('x',word(add('outside','axis')));r.set('y',word(add('inside','axis')))
    r.if_(('slt','x','y'),lambda:(r.set('n',sub('bound','x')),r.set('d',sub('y','x'))),lambda:(r.set('n',sub('x','bound')),r.set('d',sub('x','y'))))
    def interpolate(offset):r.write(add('outside',offset),invoke('wf_lerp',word(add('outside',offset)),word(add('inside',offset)),'n','d'))
    def depth_clip():
        interpolate(0);interpolate(2);r.write(add('outside',10),64)
    r.if_(('eq','axis',4),depth_clip,lambda:(r.set('other',sub(14,'axis')),interpolate('other')))
    r.write(add('outside','axis'),'bound');r.ret(1);out.append(r.finish())

    out.extend(_raster())
    out.append('__gfx_wf_ink_ref: dw __gfx_ink')
    r=NativeRoutine('cam_fast_batch',('data',),('count','i','edges','record','index','pa','pb','flags')+words('a',6)+words('b',6))
    r.set('record',word('data'));r.set('data',add('data',2))
    r.set('count',word('data'));r.set('edges',add('data',8));r.write('@__gfx_wf_near',word(add('data',4)));r.write('@__gfx_wf_far',word(add('data',6)))
    r.set('index',word(add('data',2)));r.write('@__gfx_wf_gain',add('edges',add('index','index')))
    transformed=r.label();r.emit(f'ld hl,{transformed}','push hl');r.expr('record');r.emit('jp (hl)');r.mark(transformed)
    r.set('i',0);r.set('record','@__gfx_wf_records')
    def transform():
        r.expr(invoke('wf_project','record'));r.set('record',add('record',12));r.set('i',add('i',1))
    r.while_(('ult','i','count'),transform)
    r.set('i',0);r.set('count',word(add('data',2)))
    def edge():
        for target in ('a','b'):
            r.expr(byte('edges'));r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,__gfx_wf_records','add hl,de');r._store('record')
            r.set('p'+target,'record');r.set('edges',add('edges',1))
        skip=r.label()
        r.expr(word(add('pa',10)));r.emit('ld b,l');r.expr(word(add('pb',10)));r.emit('ld a,l','and b',f'jp nz,{skip}','ld a,l','or b','ld l,a','ld h,0');r._store('flags')
        r.load('a','pa',6);r.load('b','pb',6)
        for bound,keep,bit in (('@__gfx_wf_near',1,0),('@__gfx_wf_far',0,1)):
            clean=r.label();r.expr('flags');r.emit(f'bit {bit},l',f'jp z,{clean}')
            r.if_(('eq',invoke('wf_clip',slot('a0'),slot('b0'),4,word(bound),keep),0),lambda:r.emit(f'jp {skip}'));r.mark(clean)
        for target in ('a','b'):
            r.expr(target+'5');r.emit('ld a,l','and 67')
            clean=r.label();r.emit(f'jr z,{clean}');r.expr(invoke('wf_project',slot(target+'0')));r.mark(clean)
        r.expr('a5');r.emit('ld b,l');r.expr('b5');r.emit('ld a,l','and b',f'jp nz,{skip}','ld a,l','or b','ld l,a','ld h,0');r._store('flags')
        for axis,bound,keep,bit in ((6,0,1,2),(6,95,0,3),(8,0,1,4),(8,63,0,5)):
            clean=r.label();r.expr('flags');r.emit(f'bit {bit},l',f'jp z,{clean}')
            r.if_(('eq',invoke('wf_clip',slot('a0'),slot('b0'),axis,bound,keep),0),lambda:r.emit(f'jp {skip}'));r.mark(clean)
        r.expr(invoke('wf_raster','a3','a4','b3','b4'));r.mark(skip);r.set('i',add('i',1))
    r.while_(('ult','i','count'),edge);r.ret();out.append(r.finish())
    return out


def _project():
    code=['__gfx_wf_project:','push ix','ld hl,4','add hl,sp','ld e,(hl)','inc hl','ld d,(hl)','push de','pop ix',
          'ld l,(ix+4)','ld h,(ix+5)','bit 7,h','jp nz,__gfx_wf_project_near','ld de,(__gfx_wf_near)','or a','sbc hl,de','jp c,__gfx_wf_project_near',
          'ld l,(ix+4)','ld h,(ix+5)','ld de,(__gfx_wf_far)','ex de,hl','or a','sbc hl,de','jp c,__gfx_wf_project_far',
          'ld l,(ix+4)','ld h,(ix+5)','ld b,0','__gfx_wf_project_normalize:','ld a,h','cp 2','jr c,__gfx_wf_project_gain','srl h','rr l','inc b','jr __gfx_wf_project_normalize',
          '__gfx_wf_project_gain:','ld h,0','add hl,hl','ld de,(__gfx_wf_gain)','add hl,de','ld e,(hl)','inc hl','ld d,(hl)','ex de,hl','ld a,b','or a','jr z,__gfx_wf_project_gain_ready',
          '__gfx_wf_project_gain_shift:','srl h','rr l','djnz __gfx_wf_project_gain_shift','__gfx_wf_project_gain_ready:','ld b,h','ld c,l','ld (ix+10),0','ld (ix+11),0']
    for axis in (0,2):
        n='__gfx_wf_project_'+str(axis);offset=axis+6
        code += [f'ld l,(ix+{axis})',f'ld h,(ix+{axis+1})','ld a,h','or a','push af',f'jp p,{n}_abs','xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a',n+'_abs:',
                 'ex de,hl','call __gfx_wf_umul','bit 7,h',f'jr z,{n}_rounded','inc de',n+'_rounded:','ex de,hl','pop af',f'jp p,{n}_signed','xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a',n+'_signed:']
        code += ['ld de,48','add hl,de'] if axis==0 else ['ex de,hl','ld hl,32','or a','sbc hl,de']
        code += [f'ld (ix+{offset}),l',f'ld (ix+{offset+1}),h','bit 7,h',f'jr z,{n}_positive','ld a,(ix+10)',f'or {4 if axis==0 else 16}','ld (ix+10),a',f'jr {n}_done',n+'_positive:',
                 'ld a,h','or a',f'jr nz,{n}_outside','ld a,l',f'cp {96 if axis==0 else 64}',f'jr c,{n}_done',n+'_outside:','ld a,(ix+10)',f'or {8 if axis==0 else 32}','ld (ix+10),a',n+'_done:']
    code+=['pop ix','ret','__gfx_wf_project_near:','ld (ix+10),1','ld (ix+11),0','pop ix','ret','__gfx_wf_project_far:','ld (ix+10),2','ld (ix+11),0','pop ix','ret']
    return '\n'.join(line if line.endswith(':') else '        '+line for line in code)


def _raster():
    r=NativeRoutine('wf_raster',('x1','y1','x2','y2'),('dx','dy','sx','sy','major'))
    r.set('sx',1);r.set('sy',1);r.set('dx',sub('x2','x1'));r.set('dy',sub('y2','y1'))
    r.if_(('negative','dx'),lambda:(r.set('dx',sub(0,'dx')),r.set('sx',0)))
    r.if_(('negative','dy'),lambda:(r.set('dy',sub(0,'dy')),r.set('sy',0)))
    r.set('major',0);r.if_(('ult','dx','dy'),lambda:r.set('major',1))
    # One byte pointer and rotating bit mask replace per-pixel address lookup.
    r.expr('y1');r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,0x9340','add hl,de','push hl')
    r.expr('x1');r.emit('ld a,l','and 7','ld b,a','ld a,128')
    shift,ready=r.label(),r.label();r.emit('inc b',f'jr {ready}');r.mark(shift);r.emit('rrca');r.mark(ready);r.emit(f'djnz {shift}','ld d,a')
    r.expr('x1');r.emit('srl l','srl l','srl l','pop bc','add hl,bc','push hl','push de')
    r.if_(('eq','major',0),lambda:(r.expr('dy'),r.emit('ld c,l'),r.expr('dx')),lambda:(r.expr('dx'),r.emit('ld c,l'),r.expr('dy')))
    r.emit('ld e,l','ld a,l','srl a','ld b,a','ld a,l','inc a',"ex af,af'",'pop hl','ld d,h','pop hl')
    # Dispatch once; no orientation tests or calls remain inside the pixel loop.
    for major in range(2):
        for sx in range(2):
            for sy in range(2):
                next_=r.label();r.expr('major');r.emit('ld a,l',f'cp {major}',f'jp nz,{next_}');r.expr('sx');r.emit('ld a,l',f'cp {sx}',f'jp nz,{next_}');r.expr('sy');r.emit('ld a,l',f'cp {sy}',f'jp nz,{next_}')
                # Dispatch expressions clobber HL; reconstruct the pointer only
                # here while leaving the prepared BC/DE registers untouched.
                r.emit('push bc','push de');r.expr('y1');r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,0x9340','add hl,de','push hl');r.expr('x1');r.emit('srl l','srl l','srl l','pop de','add hl,de','pop de','pop bc',f'jp __gfx_wf_octant_{major}{sx}{sy}');r.mark(next_)
    result=[r.finish()]
    def xstep(sx,name):return [f'{"rrc" if sx else "rlc"} d',f'jr nc,{name}',f'{"inc" if sx else "dec"} hl',name+':']
    def ystep(sy,name):return ['ld a,l',('add a,12' if sy else 'sub 12'),'ld l,a',f'jr nc,{name}',f'{"inc" if sy else "dec"} h',name+':']
    for major in range(2):
        for sx in range(2):
            for sy in range(2):
                n=f'__gfx_wf_octant_{major}{sx}{sy}'
                code=[n+':','ld a,(__gfx_ink)','or a','ld a,d',f'jr z,{n}_erase','or (hl)',f'jr {n}_store',n+'_erase:','cpl','and (hl)',n+'_store:','ld (hl),a',"ex af,af'",'dec a',f'jp z,{n}_done',"ex af,af'",'ld a,b','add a,c','cp e',f'jr c,{n}_straight','sub e','ld b,a']
                code+=(xstep(sx,n+'_minor') if major else ystep(sy,n+'_minor'))
                code+=[f'jr {n}_major',n+'_straight:','ld b,a',n+'_major:']
                code+=(ystep(sy,n+'_step') if major else xstep(sx,n+'_step'))
                code+=[f'jp {n}',n+'_done:',"ex af,af'",'jp __gfx_wf_raster_return']
                result.append('\n'.join(line if line.endswith(':') else '        '+line for line in code))
    return result
