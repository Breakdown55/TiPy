"""Native look-at camera, perspective divide, and pre-divide depth clipping.

Camera-space records are three signed Q18.14 words (six i16 storage words).
No projected coordinate is narrowed to i16 until viewport clipping is complete.
"""
import math
from .camera_math import NativeRoutine,invoke
from .shape_runtime import slot,words
from .list_runtime import add,sub,word,call

def field(base,index):return add(base,index*4)

def routines(batch_capacity=64):
    out=[]
    focal=[round(32/math.tan(math.radians(fov)/2)*16) for fov in range(1,180)]
    out.append('__gfx_cam_state:\n__gfx_cam_enabled: dw 0\n__gfx_cam_path_mode: dw 0\n__gfx_cam_eye: dw 0,0,80\n__gfx_cam_basis: dw 16384,0,0,0,16384,0,0,0,49152\n__gfx_cam_near: dw 16384,0\n__gfx_cam_far: dw '+str((1000<<14)&65535)+','+str((1000<<14)>>16)+'\n__gfx_cam_focal: dw '+str(focal[59])+'\n__gfx_cam_zero: dw 0,0\n__gfx_cam_right: dw 95,0\n__gfx_cam_bottom: dw 63,0')
    out.append('__gfx_cam_fov_table:\n        dw '+','.join(map(str,focal)))
    out.append('__gfx_cam_normal: defs 12,0\n__gfx_cam_forward: defs 6,0\n__gfx_cam_up: defs 6,0\n__gfx_cam_right_vector: defs 6,0\n__gfx_cam_actual_up: defs 6,0')

    r=NativeRoutine('cam_mark',('mode',))
    r.if_(('ne',word('@__gfx_cam_path_mode'),0),lambda:r.if_(('ne',word('@__gfx_cam_path_mode'),'mode'),lambda:r.fail(14)))
    r.write('@__gfx_cam_path_mode','mode');r.ret();out.append(r.finish())
    r=NativeRoutine('cam_begin',());r.write('@__gfx_cam_path_mode',0);r.ret();out.append(r.finish())
    r=NativeRoutine('cam_mode',());r.ret(word('@__gfx_cam_path_mode'));out.append(r.finish())
    r=NativeRoutine('cam_fov',('fov',))
    r.if_(('ne',word('@__gfx_cam_path_mode'),0),lambda:r.fail(14))
    r.if_(('slt','fov',1),lambda:r.fail(14));r.if_(('sge','fov',180),lambda:r.fail(14))
    r.write('@__gfx_cam_focal',word(add('@__gfx_cam_fov_table',add(sub('fov',1),sub('fov',1)))))
    r.write('@__gfx_cam_enabled',1);r.ret();out.append(r.finish())
    r=NativeRoutine('cam_init',('fov','near','far'),words('v',2))
    r.if_(('slt','near',1),lambda:r.fail(14));r.if_(('sge','near','far'),lambda:r.fail(14))
    r.expr(invoke('cam_fov','fov'))
    for name in ('near','far'):
        r.extend('v',name)
        for _ in range(14):r.shift('v',False)
        r.save('v','@__gfx_cam_'+name)
    r.ret();out.append(r.finish())

    # Normalize a signed 32-bit vector into signed Q1.14 components.
    r=NativeRoutine('cam_normalize',('input','out'),words('x',2)+words('y',2)+words('z',2)+words('sum',2)+words('p',4)+words('den',2)+words('q',2)+('sx','sy','sz','shrink','lo','hi','mid'))
    for i,name in enumerate('xyz'):
        r.load(name,field('input',i));r.set('s'+name,0)
        r.if_(('negative',name+'1'),lambda name=name:(r.negate(name),r.set('s'+name,1)))
    start,ready=r.label(),r.label();r.mark(start);r.set('shrink',0)
    for name in 'xyz':
        r.if_(('ne',name+'1',0),lambda:r.set('shrink',1));r.if_(('uge',name+'0',16384),lambda:r.set('shrink',1))
    r.false_jump(('ne','shrink',0),ready)
    for name in 'xyz':r.shift(name)
    r.emit(f'jp {start}');r.mark(ready)
    r.if_(('eq','x0',0),lambda:r.if_(('eq','y0',0),lambda:r.if_(('eq','z0',0),lambda:r.fail(14))))
    # Use the available precision even for short direction vectors such as (1,1,0).
    grow,normalized=r.label(),r.label();r.mark(grow)
    for name in 'xyz':r.if_(('uge',name+'0',8192),lambda:r.emit(f'jp {normalized}'))
    for name in 'xyz':r.shift(name,False)
    r.emit(f'jp {grow}');r.mark(normalized);r.zero('sum')
    for name in 'xyz':
        r.expr(invoke('u_mul32',name+'0',0,name+'0',0,slot('p0')));r.binary('sum','sum','p')
    r.if_(('eq','sum0',0),lambda:r.if_(('eq','sum1',0),lambda:r.fail(14)))
    r.set('lo',0);r.set('hi',28377)
    def sqrt_step():
        r.expr(add(add('lo','hi'),1));r.emit('srl h','rr l');r._store('mid')
        r.expr(invoke('u_mul32','mid',0,'mid',0,slot('p0')))
        less,greater,done=r.label(),r.label(),r.label();r.compare('p','sum',less,less,greater,False)
        r.mark(less);r.set('lo','mid');r.emit(f'jp {done}');r.mark(greater);r.set('hi',sub('mid',1));r.mark(done)
    r.while_(('ult','lo','hi'),sqrt_step);r.set('den0','lo');r.set('den1',0)
    for i,name in enumerate('xyz'):
        r.expr(invoke('u_mul32',name+'0',0,16384,0,slot('p0')))
        r.expr(invoke('u_div64',slot('p0'),slot('den0'),slot('q0'),0))
        r.if_(('ne','s'+name,0),lambda:r.negate('q'));r.write(add('out',2*i),'q0')
    r.ret();out.append(r.finish())

    r=NativeRoutine('cam_cross',('a','b','out'),words('x',2)+words('y',2)+words('p',4)+words('q',4))
    for axis,(j,k) in enumerate(((1,2),(2,0),(0,1))):
        r.extend('x',word(add('a',j*2)));r.extend('y',word(add('b',k*2)));r.expr(invoke('s_mul32',slot('x0'),slot('y0'),slot('p0')))
        r.extend('x',word(add('a',k*2)));r.extend('y',word(add('b',j*2)));r.expr(invoke('s_mul32',slot('x0'),slot('y0'),slot('q0')))
        r.binary('p','p','q',True);r.save('p',field('out',axis))
    r.ret();out.append(r.finish())

    r=NativeRoutine('cam_look',('ex','ey','ez','tx','ty','tz','ux','uy','uz'),words('a',2)+words('b',2)+words('v',2))
    r.if_(('ne',word('@__gfx_cam_path_mode'),0),lambda:r.fail(14))
    for i,axis in enumerate('xyz'):
        r.extend('a','t'+axis);r.extend('b','e'+axis);r.binary('v','a','b',True);r.save('v',field('@__gfx_cam_normal',i))
    r.expr(invoke('cam_normalize','@__gfx_cam_normal','@__gfx_cam_forward'))
    for i,axis in enumerate('xyz'):r.extend('v','u'+axis);r.save('v',field('@__gfx_cam_normal',i))
    r.expr(invoke('cam_normalize','@__gfx_cam_normal','@__gfx_cam_up'))
    r.expr(invoke('cam_cross','@__gfx_cam_forward','@__gfx_cam_up','@__gfx_cam_normal'))
    r.expr(invoke('cam_normalize','@__gfx_cam_normal','@__gfx_cam_right_vector'))
    r.expr(invoke('cam_cross','@__gfx_cam_right_vector','@__gfx_cam_forward','@__gfx_cam_normal'))
    r.expr(invoke('cam_normalize','@__gfx_cam_normal','@__gfx_cam_actual_up'))
    for offset,name in ((0,'right_vector'),(6,'actual_up'),(12,'forward')):
        r.emit('ld hl,__gfx_cam_'+name,f'ld de,__gfx_cam_basis+{offset}','ld bc,6','ldir')
    for i,axis in enumerate('xyz'):r.write(add('@__gfx_cam_eye',i*2),'e'+axis)
    r.write('@__gfx_cam_enabled',1);r.ret();out.append(r.finish())

    r=NativeRoutine('cam_view',('x','y','z','out'),('wx','wy','wz','row','base')+words('dx',2)+words('dy',2)+words('dz',2)+words('c',2)+words('p',4)+words('sum',4))
    for i,axis in enumerate('xyz'):r.set('w'+axis,invoke('model_axis','x','y','z',i))
    def orthographic():
        for i,axis in enumerate('xyz'):
            r.extend('c','w'+axis)
            for _ in range(14):r.shift('c',False)
            r.save('c',field('out',i))
        r.ret()
    r.if_(('eq',word('@__gfx_cam_enabled'),0),orthographic)
    for i,axis in enumerate('xyz'):
        r.extend('d'+axis,'w'+axis);r.extend('c',word(add('@__gfx_cam_eye',i*2)));r.binary('d'+axis,'d'+axis,'c',True)
    r.set('row',0);r.set('base','@__gfx_cam_basis')
    def row():
        r.zero('sum',4)
        for i,axis in enumerate('xyz'):
            skip=r.label();r.extend('c',word(add('base',2*i)))
            r.expr('c0');r.emit('ld a,h','or l',f'jp z,{skip}')
            r.expr('d'+axis+'0');r.emit('ld a,h','or l');r.expr('d'+axis+'1');r.emit('or h','or l',f'jp z,{skip}')
            r.expr(invoke('s_mul32',slot('d'+axis+'0'),slot('c0'),slot('p0')));r.binary('sum','sum','p',count=4,checked=True);r.mark(skip)
        r.save('sum','out');r.set('out',add('out',4));r.set('base',add('base',6));r.set('row',add('row',1))
    r.while_(('ult','row',3),row);r.ret();out.append(r.finish())

    # A 64/64 divider with a 32-bit quotient, for subpixel focal lengths.
    r=NativeRoutine('project_div',('n','d','out'),words('q',2)+words('rem',4)+words('den',4))
    def narrow_divide():r.expr(invoke('u_div32','n','d','out',0));r.ret()
    def narrow_numerator():
        r.if_(('eq',word(add('n',4)),0),lambda:r.if_(('eq',word(add('n',6)),0),narrow_divide))
    r.if_(('eq',word(add('d',4)),0),lambda:r.if_(('eq',word(add('d',6)),0),narrow_numerator))
    r.load('q','n');r.zero('rem',4);r.load('rem',add('n',4));r.load('den','d',4)
    good,bad=r.label(),r.label();r.compare('rem','den',good,bad,bad,False,4);r.mark(bad);r.fail(14);r.mark(good)
    loop,subtract,keep,again=r.label(),r.label(),r.label(),r.label();r.emit('ld b,32');r.mark(loop);r.shift('q',False)
    for i in range(8):r.emit(f'rl (ix{r.slots["rem0"]+i:+d})')
    r.emit(f'jp c,{subtract}');r.compare('rem','den',keep,subtract,subtract,False,4)
    r.mark(subtract);r.binary('rem','rem','den',True,4);r.emit(f'set 0,(ix{r.slots["q0"]:+d})');r.mark(keep);r.mark(again);r.emit('dec b',f'jp nz,{loop}')
    r.save('q','out');r.ret();out.append(r.finish())

    out.append('''__gfx_projection_cache_count: dw 0
__gfx_projection_cache_mode: dw 0
__gfx_projection_cache_focal: dw 0
__gfx_projection_cache_next: dw __gfx_projection_cache_records
__gfx_projection_cache_records: defs 320,0
__gfx_projection_cache_end:''')
    r=NativeRoutine('cam_project',('point',),('record','i'))
    def reset_projection_cache():
        r.write('@__gfx_projection_cache_count',0);r.write('@__gfx_projection_cache_next','@__gfx_projection_cache_records')
        r.write('@__gfx_projection_cache_mode',word('@__gfx_cam_enabled'));r.write('@__gfx_projection_cache_focal',word('@__gfx_cam_focal'))
    r.if_(('ne',word('@__gfx_projection_cache_mode'),word('@__gfx_cam_enabled')),reset_projection_cache)
    r.if_(('ne',word('@__gfx_projection_cache_focal'),word('@__gfx_cam_focal')),reset_projection_cache)
    r.set('record','@__gfx_projection_cache_records');r.set('i',0)
    def probe():
        mismatch,compare=r.label(),r.label()
        r.expr('point');r.emit('push hl');r.expr('record');r.emit('ex de,hl','pop hl','ld b,12');r.mark(compare)
        r.emit('ld a,(de)','cp (hl)',f'jp nz,{mismatch}','inc hl','inc de',f'djnz {compare}')
        r.expr('point');r.emit('ex de,hl','ld bc,8','ldir');r.ret()
        r.mark(mismatch);r.set('record',add('record',20));r.set('i',add('i',1))
    r.while_(('ult','i',word('@__gfx_projection_cache_count')),probe)
    r.set('record',word('@__gfx_projection_cache_next'))
    r.expr('point');r.emit('push hl');r.expr('record');r.emit('ex de,hl','pop hl','ld bc,12','ldir')
    r.expr(invoke('cam_project_compute','point'))
    r.expr('point');r.emit('push hl');r.expr(add('record',12));r.emit('ex de,hl','pop hl','ld bc,8','ldir')
    r.set('record',add('record',20));r.if_(('eq','record','@__gfx_projection_cache_end'),lambda:r.set('record','@__gfx_projection_cache_records'))
    r.write('@__gfx_projection_cache_next','record')
    r.if_(('ult',word('@__gfx_projection_cache_count'),16),lambda:r.write('@__gfx_projection_cache_count',add(word('@__gfx_projection_cache_count'),1)))
    r.ret();out.append(r.finish())

    r=NativeRoutine('cam_project_compute',('point',),words('v',2)+words('focal',2)+words('n',4)+words('den',4)+words('half',4)+words('q',2)+words('center',2)+('negative',))
    def ortho():
        for axis in (0,1):
            r.load('v',field('point',axis))
            for _ in range(14):r.shift('v',signed=True)
            r.save('v',field('point',axis))
        r.ret()
    r.if_(('eq',word('@__gfx_cam_enabled'),0),ortho)
    r.zero('den',4);r.load('den',field('point',2))
    for _ in range(4):r.shift('den',False,count=4)
    for i in range(4):r.set('half'+str(i),'den'+str(i))
    r.shift('half',count=4);r.set('focal0',word('@__gfx_cam_focal'));r.set('focal1',0)
    for axis in (0,1):
        r.load('v',field('point',axis));r.set('negative',0);r.if_(('negative','v1'),lambda:(r.negate('v'),r.set('negative',1)))
        r.expr(invoke('u_mul32','v0','v1','focal0','focal1',slot('n0')));r.binary('n','n','half',count=4)
        r.expr(invoke('project_div',slot('n0'),slot('den0'),slot('q0')));r.if_(('ne','negative',0),lambda:r.negate('q'))
        r.set('center0',48 if axis==0 else 32);r.set('center1',0);r.binary('v','center','q',subtract=axis==1);r.save('v',field('point',axis))
    r.ret();out.append(r.finish())

    r=NativeRoutine('cam_inside',('point','axis','bound','keep'),words('a',2)+words('b',2)+('address',))
    r.expr('axis');r.emit('add hl,hl','add hl,hl');r._store('address');r.load('a',add('point','address'));r.load('b','bound')
    lt,eq,gt=r.label(),r.label(),r.label();r.compare('a','b',lt,eq,gt);r.mark(lt);r.ret(sub(1,'keep'));r.mark(eq);r.ret(1);r.mark(gt);r.ret('keep');out.append(r.finish())

    r=NativeRoutine('cam_clip_line',('a','b','axis','bound','keep'),('ia','ib','outside','inside','offset','index')+words('v',2)+words('w',2)+words('plane',2)+words('n',2)+words('d',2))
    r.set('ia',invoke('cam_inside','a','axis','bound','keep'));r.set('ib',invoke('cam_inside','b','axis','bound','keep'))
    r.if_(('eq',add('ia','ib'),0),lambda:r.ret(0));r.if_(('eq',add('ia','ib'),2),lambda:r.ret(1))
    r.set('outside','a');r.set('inside','b');r.if_(('ne','ia',0),lambda:(r.set('outside','b'),r.set('inside','a')))
    r.expr('axis');r.emit('add hl,hl','add hl,hl');r._store('offset')
    r.load('v',add('outside','offset'));r.load('w',add('inside','offset'));r.load('plane','bound')
    for other,target in (('w','d'),('plane','n')):
        lt,gt,end=r.label(),r.label(),r.label();r.compare('v',other,lt,lt,gt);r.mark(lt);r.binary(target,other,'v',True);r.emit(f'jp {end}');r.mark(gt);r.binary(target,'v',other,True);r.mark(end)
    r.set('index',0)
    def coordinate():
        r.if_(('ne','index','offset'),lambda:r.expr(invoke('lerp32',add('outside','index'),add('inside','index'),slot('n0'),slot('d0'),add('outside','index'))));r.set('index',add('index',4))
    r.while_(('ult','index',12),coordinate);r.save('plane',add('outside','offset'));r.ret(1);out.append(r.finish())

    r=NativeRoutine('cam_prepare_line',('a','b','width'),('half',)+words('bound',2))
    for axis,bound,keep in ((2,'near',1),(2,'far',0)):
        r.if_(('ne',word('@__gfx_cam_enabled'),0),lambda axis=axis,bound=bound,keep=keep:r.if_(('eq',invoke('cam_clip_line','a','b',axis,'@__gfx_cam_'+bound,keep),0),lambda:r.ret(0)))
    r.expr(invoke('cam_project','a'));r.expr(invoke('cam_project','b'))
    # Preserve brush pixels whose center lies just outside the viewport.
    r.expr('width');r.emit('srl h','rr l');r._store('half')
    for axis,edge,keep in ((0,0,1),(0,95,0),(1,0,1),(1,63,0)):
        r.extend('bound',sub(0,sub(sub('width','half'),1)) if keep else add(edge,'half'))
        r.if_(('eq',invoke('cam_clip_line','a','b',axis,slot('bound0'),keep),0),lambda:r.ret(0))
    r.ret(1);out.append(r.finish())

    r=NativeRoutine('cam_screen_line',('a','b','width'),('low','high','dx','dy','half'))
    r.if_(('slt','width',1),lambda:r.ret());r.if_(('sge','width',192),lambda:r.set('width',191))
    r.if_(('eq',invoke('cam_prepare_line','a','b','width'),0),lambda:r.ret())
    r.expr('width');r.emit('srl h','rr l');r._store('half');r.set('low',sub(0,'half'));r.set('high',sub(sub('width','half'),1));r.set('dy','low')
    def row():
        r.set('dx','low')
        def pixel():
            r.expr(invoke('styled_line',add(word('a'),'dx'),add(word(add('a',4)),'dy'),add(word('b'),'dx'),add(word(add('b',4)),'dy')));r.set('dx',add('dx',1))
        r.while_(('slt','dx',add('high',1)),pixel);r.set('dy',add('dy',1))
    r.while_(('slt','dy',add('high',1)),row);r.ret();out.append(r.finish())

    r=NativeRoutine('cam_line',('x1','y1','z1','x2','y2','z2','width'),words('a',6)+words('b',6))
    r.expr(invoke('cam_view','x1','y1','z1',slot('a0')));r.expr(invoke('cam_view','x2','y2','z2',slot('b0')))
    r.expr(invoke('cam_screen_line',slot('a0'),slot('b0'),'width'));r.ret();out.append(r.finish())

    out.append('__gfx_batch_records: defs '+str(batch_capacity*12)+',0')
    r=NativeRoutine('cam_batch',('data','width'),('vertices','edges','count','i','record','edge','index')+words('a',6)+words('b',6))
    r.set('count',word('data'));r.set('vertices',add('data',4));r.set('record','@__gfx_batch_records');r.set('i',0)
    def transform():
        r.expr(invoke('cam_view',word('vertices'),word(add('vertices',2)),word(add('vertices',4)),'record'))
        r.set('vertices',add('vertices',6));r.set('record',add('record',12));r.set('i',add('i',1))
    r.while_(('ult','i','count'),transform)
    r.set('edges','vertices');r.set('count',word(add('data',2)));r.set('i',0)
    def draw_edge():
        for target in ('a','b'):
            r.set('index',('byte','edges'));r.expr('index');r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,__gfx_batch_records','add hl,de');r._store('record')
            r.load(target,'record',6);r.set('edges',add('edges',1))
        r.expr(invoke('cam_screen_line',slot('a0'),slot('b0'),'width'));r.set('i',add('i',1))
    r.while_(('ult','i','count'),draw_edge);r.ret();out.append(r.finish())

    r=NativeRoutine('cam_vertex',('x','y','z'),words('a',6))
    r.expr(invoke('cam_view','x','y','z',slot('a0')));r.ret(call('from_data',2,6,slot('a0')));out.append(r.finish())

    r=NativeRoutine('cam_point',('x','y','z','width'),words('a',6)+words('b',6))
    r.expr(invoke('cam_view','x','y','z',slot('a0')))
    for i in range(6):r.set('b'+str(i),'a'+str(i))
    r.expr(invoke('cam_screen_line',slot('a0'),slot('b0'),'width'));r.ret();out.append(r.finish())

    r=NativeRoutine('cam_edges',('points','closed','width'),('n','i','j','limit','record')+words('a',6)+words('b',6))
    r.set('n',word(add('points',2)));r.if_(('eq','n',0),lambda:r.ret())
    r.set('limit',sub('n',1));r.if_(('ne','closed',0),lambda:r.set('limit','n'));r.if_(('eq','n',1),lambda:r.set('limit',1));r.set('i',0)
    def edge():
        r.set('j',add('i',1));r.if_(('eq','j','n'),lambda:r.set('j',0))
        for index,target in (('i','a'),('j','b')):
            r.set('record',word(add(word('points'),add(index,index))));r.load(target,word('record'),6)
        r.expr(invoke('cam_screen_line',slot('a0'),slot('b0'),'width'));r.set('i',add('i',1))
    r.while_(('ult','i','limit'),edge);r.ret();out.append(r.finish())

    r=NativeRoutine('cam_clip_poly',('points','axis','bound','keep'),('result','n','i','previous','current','was_inside','inside','record'))
    r.set('result',call('new',32765));r.set('n',word(add('points',2)));r.if_(('eq','n',0),lambda:r.ret('result'))
    r.set('previous',word(add(word('points'),add(sub('n',1),sub('n',1)))))
    r.set('was_inside',invoke('cam_inside',word('previous'),'axis','bound','keep'));r.set('i',0)
    def intersection(outside,inside):
        r.set('record',call('copy',outside));r.expr(invoke('cam_clip_line',word('record'),word(inside),'axis','bound','keep'))
        r.expr(call('append','result','record'));r.expr(call('release','record'))
    def vertex():
        r.set('current',word(add(word('points'),add('i','i'))));r.set('inside',invoke('cam_inside',word('current'),'axis','bound','keep'))
        def entering():
            r.if_(('eq','was_inside',0),lambda:intersection('previous','current'));r.expr(call('append','result','current'))
        r.if_(('ne','inside',0),entering,lambda:r.if_(('ne','was_inside',0),lambda:intersection('current','previous')))
        r.set('previous','current');r.set('was_inside','inside');r.set('i',add('i',1))
    r.while_(('ult','i','n'),vertex);r.ret('result');out.append(r.finish())

    r=NativeRoutine('cam_project_poly',('points',),('result','n','i','record'))
    r.set('result',call('new',32765));r.set('n',word(add('points',2)));r.set('i',0)
    def vertex():
        r.set('record',call('copy',word(add(word('points'),add('i','i')))));r.expr(invoke('cam_project',word('record')))
        r.expr(call('append','result','record'));r.expr(call('release','record'));r.set('i',add('i',1))
    r.while_(('ult','i','n'),vertex);r.ret('result');out.append(r.finish())

    r=NativeRoutine('cam_fill',('points',),('current','next','raster','n','i','record','tuple'))
    r.set('current',call('copy','points'))
    def clip(axis,bound,keep):
        r.set('next',invoke('cam_clip_poly','current',axis,'@__gfx_cam_'+bound,keep));r.expr(call('release','current'));r.set('current','next')
    for axis,bound,keep in ((2,'near',1),(2,'far',0)):
        r.if_(('ne',word('@__gfx_cam_enabled'),0),lambda axis=axis,bound=bound,keep=keep:clip(axis,bound,keep))
    r.set('next',invoke('cam_project_poly','current'));r.expr(call('release','current'));r.set('current','next')
    for axis,bound,keep in ((0,'zero',1),(0,'right',0),(1,'zero',1),(1,'bottom',0)):clip(axis,bound,keep)
    r.set('raster',call('new',32765));r.set('n',word(add('current',2)));r.set('i',0)
    def vertex():
        r.set('record',word(add(word('current'),add('i','i'))));r.set('tuple',call('tuple_new','@__gfx_cam_tuple_flags',2))
        r.expr(call('tuple_set','tuple',0,word(word('record'))));r.expr(call('tuple_set','tuple',1,word(add(word('record'),4))))
        r.expr(call('append','raster','tuple'));r.expr(call('release','tuple'));r.set('i',add('i',1))
    r.while_(('ult','i','n'),vertex);r.expr(invoke('polygon','raster',1));r.expr(call('release','raster'));r.expr(call('release','current'));r.ret()
    out.append(r.finish()+'\n__gfx_cam_tuple_flags: db 0,0')
    return out
