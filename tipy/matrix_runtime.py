"""Q16.16 model matrices for camera-enabled programs; no host arithmetic."""
from .camera_math import NativeRoutine,invoke
from .shape_runtime import words,slot
from .list_runtime import add,sub,word,call

def routines():
    out=[]
    identity=[v for i in range(16) for v in (0,int(i%5==0))]
    out.append('__gfx_model:\n        dw '+','.join(map(str,identity))+'\n__gfx_model_changed: dw 0\n__gfx_model_stack: dw 0\n__gfx_rhs: defs 64,0\n__gfx_saved: defs 64,0')
    r=NativeRoutine('mat_identity',('out',),('i',))
    r.expr('out');r.emit('push hl','pop de','inc de','ld (hl),0','ld bc,63','ldir');r.set('i',0)
    def diagonal():r.write(add('out',add('i',2)),1);r.set('i',add('i',20))
    r.while_(('ult','i',64),diagonal);r.ret();out.append(r.finish())

    r=NativeRoutine('mat_multiply',(),('row','column','k','a','b','out')+words('sum',4)+words('product',4)+words('translation',4))
    r.emit('ld hl,__gfx_model','ld de,__gfx_saved','ld bc,64','ldir');r.set('row',0);r.set('out','@__gfx_model')
    def row():
        r.set('column',0)
        def column():
            r.zero('sum',4);r.set('a',add('@__gfx_saved','row'));r.set('b',add('@__gfx_rhs','column'));r.set('k',0)
            def term():
                r.expr(invoke('s_mul32','a','b',slot('product0')));r.binary('sum','sum','product',count=4,checked=True)
                r.set('a',add('a',4));r.set('b',add('b',16));r.set('k',add('k',1))
            r.while_(('ult','k',4),term);r.expr(invoke('fixed_store',slot('sum0'),'out'));r.set('out',add('out',4));r.set('column',add('column',4))
        r.while_(('ult','column',16),column);r.set('row',add('row',16))
    r.while_(('ult','row',64),row);r.write('@__gfx_model_changed',1);r.ret();out.append(r.finish())

    for name in ('translate','scale'):
        r=NativeRoutine('mat_'+name,('x0','x1','y0','y1','z0','z1'))
        r.expr(invoke('mat_identity','@__gfx_rhs'))
        for axis,offset in zip('xyz',(12,28,44) if name=='translate' else (0,20,40)):
            r.write(add('@__gfx_rhs',offset),axis+'0');r.write(add('@__gfx_rhs',offset+2),axis+'1')
        r.expr(invoke('mat_multiply'));r.ret();out.append(r.finish())

    r=NativeRoutine('mat_apply',tuple('v'+str(i) for i in range(32)))
    for i in range(32):r.write(add('@__gfx_rhs',2*i),'v'+str(i))
    r.expr(invoke('mat_multiply'));r.ret();out.append(r.finish())

    r=NativeRoutine('mat_reset',());r.expr(invoke('mat_identity','@__gfx_model'));r.write('@__gfx_model_changed',0);r.ret();out.append(r.finish())
    r=NativeRoutine('mat_push',(),('record',))
    r.if_(('eq',word('@__gfx_model_stack'),0),lambda:r.write('@__gfx_model_stack',call('new',32765)))
    r.set('record',call('from_data',2,33,'@__gfx_model'));r.expr(call('append',word('@__gfx_model_stack'),'record'));r.expr(call('release','record'));r.ret();out.append(r.finish())
    r=NativeRoutine('mat_pop',(),('record',))
    r.if_(('eq',word('@__gfx_model_stack'),0),lambda:r.fail(2));r.set('record',call('pop',word('@__gfx_model_stack'),-1))
    r.expr(word('record'));r.emit('ld de,__gfx_model','ld bc,66','ldir');r.expr(call('release','record'));r.ret();out.append(r.finish())

    r=NativeRoutine('model_axis',('x','y','z','axis'),('i','coefficient')+words('v',2)+words('p',4)+words('sum',4))
    def identity_axis():
        r.if_(('eq','axis',0),lambda:r.ret('x'));r.if_(('eq','axis',1),lambda:r.ret('y'));r.ret('z')
    r.if_(('eq',word('@__gfx_model_changed'),0),identity_axis)
    r.expr('axis');r.emit('add hl,hl','add hl,hl','add hl,hl','add hl,hl','ld de,__gfx_model','add hl,de');r._store('coefficient');r.zero('sum',4)
    for value in 'xyz':
        r.extend('v',value);r.expr(invoke('s_mul32','coefficient',slot('v0'),slot('p0')));r.binary('sum','sum','p',count=4,checked=True);r.set('coefficient',add('coefficient',4))
    r.zero('p',4);r.load('p','coefficient');r.if_(('negative','p1'),lambda:(r.set('p2',65535),r.set('p3',65535)))
    r.binary('sum','sum','p',count=4,checked=True);r.ret(invoke('round_coord',slot('sum0')));out.append(r.finish())

    r=NativeRoutine('mat_from_float',('object',),words('m',2)+('negative','exponent','count','top'))
    r.load('m',word('object'));r.set('negative',0);r.if_(('negative','m1'),lambda:r.set('negative',1))
    r.expr('m1');r.emit('ld a,h','and 127','ld h,a','srl h','rr l','srl h','rr l','srl h','rr l','srl h','rr l','srl h','rr l','srl h','rr l','srl h','rr l');r._store('exponent')
    r.expr('m1');r.emit('ld a,l','and 127','or 128','ld l,a','ld h,0');r._store('m1')
    def too_big():
        good=r.label();r.false_jump(('eq','exponent',142),good)
        r.if_(('ne','negative',0),lambda:r.if_(('eq','m1',128),lambda:r.if_(('eq','m0',0),lambda:(r.set('m0',0),r.set('m1',32768),r.emit(f'jp {r.name}_pair')))))
        r.mark(good);r.fail(14)
    r.if_(('uge','exponent',142),too_big)
    def small():
        r.set('count',sub(134,'exponent'));r.if_(('uge','count',32),lambda:(r.zero('m'),r.set('count',0)))
        r.while_(('ne','count',0),lambda:(r.shift('m'),r.set('count',sub('count',1))))
    def large():
        r.set('count',sub('exponent',134));r.while_(('ne','count',0),lambda:(r.shift('m',False),r.set('count',sub('count',1))))
    r.if_(('ult','exponent',134),small,large);r.if_(('ne','negative',0),lambda:r.negate('m'))
    r.mark(r.name+'_pair');r.expr('m1');r.emit('ex de,hl');r.expr('m0');r.emit(f'jp {r.name}_return');out.append(r.finish())

    r=NativeRoutine('mat_rotate',('a0','a1','axis'),words('angle',2)+words('n',4)+words('period',2)+words('q',2)+words('rem',2)+words('s',2)+words('c',2)+('negative','phase','sin','cos'))
    r.set('angle0','a0');r.set('angle1','a1');r.set('negative',0);r.if_(('negative','angle1'),lambda:(r.negate('angle'),r.set('negative',1)))
    r.zero('n',4);r.set('n0','angle0');r.set('n1','angle1');r.set('period0',411775&65535);r.set('period1',411775>>16)
    r.expr(invoke('u_div64',slot('n0'),slot('period0'),slot('q0'),slot('rem0')))
    for _ in range(8):r.shift('rem')
    r.set('phase','rem0');r.set('sin',invoke('sin_raw','phase'));r.set('cos',invoke('sin_raw',add('phase',402)))
    r.if_(('ne','negative',0),lambda:r.set('sin',sub(0,'sin')))
    for value,prefix in (('sin','s'),('cos','c')):
        r.extend(prefix,value)
        for _ in range(8):r.shift(prefix,False)
    r.expr(invoke('mat_identity','@__gfx_rhs'))
    def set_cell(index,prefix,negative=False):
        if negative:r.negate(prefix)
        r.write(add('@__gfx_rhs',index*4),prefix+'0');r.write(add('@__gfx_rhs',index*4+2),prefix+'1')
        if negative:r.negate(prefix)
    def axis(first,second):
        set_cell(first*4+first,'c');set_cell(second*4+second,'c');set_cell(first*4+second,'s',True);set_cell(second*4+first,'s')
    r.if_(('eq','axis',0),lambda:axis(1,2),lambda:r.if_(('eq','axis',1),lambda:axis(2,0),lambda:axis(0,1)))
    r.expr(invoke('mat_multiply'));r.ret();out.append(r.finish())
    r=NativeRoutine('model_contour',('x','y','rx','ry'),('result','i','tuple','px','py')+words('a',2)+words('b',2)+words('p',4))
    r.set('result',call('new',32765))
    r.if_(('negative','rx'),lambda:r.ret('result'));r.if_(('negative','ry'),lambda:r.ret('result'));r.set('i',0)
    def vertex():
        for radius,offset,target,center in (('rx',0,'px','x'),('ry',2,'py','y')):
            r.extend('a',radius)
            index=add(add('i','i'),add('i','i'))
            r.extend('b',word(add('@__gfx_contour_table',add(index,offset))))
            r.expr(invoke('s_mul32',slot('a0'),slot('b0'),slot('p0')))
            for _ in range(8):r.shift('p',False,count=4)
            r.set(target,invoke('sat_add',center,invoke('round_coord',slot('p0'))))
        r.set('tuple',call('tuple_new','@__gfx_contour_flags',2));r.expr(call('tuple_set','tuple',0,'px'));r.expr(call('tuple_set','tuple',1,'py'))
        r.expr(call('append','result','tuple'));r.expr(call('release','tuple'));r.set('i',add('i',1))
    r.while_(('ult','i',32),vertex);r.ret('result')
    import math
    values=[round(fn(i*math.pi/16)*256)&65535 for i in range(32) for fn in (math.cos,math.sin)]
    out.append(r.finish()+'\n__gfx_contour_flags: db 0,0\n__gfx_contour_table:\n        dw '+','.join(map(str,values)))
    return out
