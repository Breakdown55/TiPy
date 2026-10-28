"""Four additive world-space lights; native per-face Lambert + linear falloff."""
from .camera_math import NativeRoutine,invoke
from .list_runtime import add,sub,word,byte


def routines(points=True,quads=False):
    normal_offset=6 if quads else 4
    out=['__gfx_light_count: dw 0\n__gfx_light_points: dw 0',
         '__gfx_light_records: defs 48,0',
         '__gfx_light_normal: defs 6,0\n__gfx_light_center: defs 6,0\n__gfx_light_delta: defs 6,0\n__gfx_light_unit: defs 6,0']
    r=NativeRoutine('solid_no_lights')
    for name in ('@__gfx_light_count','@__gfx_light_points','@__gfx_solid_direction_enabled','@__gfx_solid_ambient'):r.write(name,0)
    r.ret();out.append(r.finish())
    r=NativeRoutine('solid_ambient_light',('level',))
    r.if_(('slt','level',0),lambda:r.fail(14));r.if_(('slt',16,'level'),lambda:r.fail(14))
    r.write('@__gfx_solid_ambient','level');r.ret();out.append(r.finish())
    r=NativeRoutine('light_acquire',('intensity',),('count',))
    r.if_(('slt','intensity',0),lambda:r.fail(14));r.if_(('slt',16,'intensity'),lambda:r.fail(14))
    r.set('count',word('@__gfx_light_count'));r.if_(('uge','count',4),lambda:r.fail(14))
    r.write('@__gfx_light_count',add('count',1));r.write('@__gfx_solid_direction_enabled',0)
    r.ret(add('@__gfx_light_records',invoke('mesh_imul','count',12)));out.append(r.finish())
    r=NativeRoutine('solid_directional_light',('x','y','z','intensity'),('p',))
    for n in 'xyz':
        r.if_(('slt',n,-2047),lambda:r.fail(14));r.if_(('slt',2047,n),lambda:r.fail(14))
    r.set('p',invoke('light_acquire','intensity'));r.write('p',1);r.write(add('p',8),'intensity')
    r.expr(invoke('mesh_normalize','x','y','z',add('p',2)));r.ret();out.append(r.finish())
    r=NativeRoutine('solid_directional_coeff',('x','y','z','intensity'),('p',))
    r.set('p',invoke('light_acquire','intensity'));r.write('p',1)
    for i,n in enumerate(('x','y','z','intensity')):r.write(add('p',2+i*2),n)
    r.ret();out.append(r.finish())
    r=NativeRoutine('solid_point_light',('x','y','z','radius','intensity'),('p',))
    r.if_(('slt','radius',1),lambda:r.fail(14));r.if_(('slt',1024,'radius'),lambda:r.fail(14))
    for n in 'xyz':
        r.if_(('slt',n,-512),lambda:r.fail(14));r.if_(('slt',512,n),lambda:r.fail(14))
    r.set('p',invoke('light_acquire','intensity'));r.write('p',2)
    r.if_(('slt',1,'intensity'),lambda:r.write('@__gfx_light_points',1))
    for i,n in enumerate(('x','y','z','intensity','radius')):r.write(add('p',2+i*2),n)
    r.ret();out.append(r.finish())
    r=NativeRoutine('light_div3',('v',),('negative',))
    r.set('negative',0);r.if_(('negative','v'),lambda:(r.set('v',sub(0,'v')),r.set('negative',1)))
    r.expr(add('v',1));r.emit('ld de,0','ld bc,3','call __gfx_wf_udiv');r._store('v')
    r.if_(('ne','negative',0),lambda:r.ret(sub(0,'v')));r.ret('v');out.append(r.finish())
    r=NativeRoutine('light_centroid',('face',),('i','p','v','sum','axis'))
    for axis in range(3):
        r.set('sum',0)
        for i in range(3):
            r.expr(byte(add('face',i)));r.emit('add hl,hl','push hl','add hl,hl','pop de','add hl,de','push hl');r.expr(word('@__gfx_mesh_source'));r.emit('pop de','add hl,de');r._store('p')
            r.set('sum',add('sum',word(add('p',axis*2))))
        r.write(add('@__gfx_mesh_vertex',axis*2),invoke('light_div3','sum'))
    for axis in range(3):
        r.set('v',invoke('mesh_vertex_dot',add('@__gfx_mesh_rotation',axis*6),'@__gfx_mesh_vertex'))
        r.expr(invoke('mesh_mul','v',word('@__gfx_mesh_instance+6')));r.emit('ld l,h','ld h,0','bit 7,l')
        positive=r.label();r.emit(f'jr z,{positive}','ld h,255');r.mark(positive);r._store('v')
        r.write(add('@__gfx_light_center',axis*2),add('v',word(add('@__gfx_mesh_instance',axis*2))))
    r.ret();out.append(r.finish())
    r=NativeRoutine('light_point',('p',),('i','v','distance','radius','intensity','max'))
    r.set('radius',word(add('p',10)));r.set('intensity',word(add('p',8)));r.set('max',0)
    # Linear integer falloff is strictly below one away from the source, and
    # zero distance already returns zero. Intensities 0/1 cannot contribute.
    r.if_(('ult','intensity',2),lambda:r.ret(0))
    for i in range(3):
        r.set('v',sub(word(add('p',2+i*2)),word(add('@__gfx_light_center',i*2))))
        r.write(add('@__gfx_light_delta',i*2),'v');r.if_(('negative','v'),lambda:r.set('v',sub(0,'v')))
        r.if_(('uge','v','radius'),lambda:r.ret(0));r.if_(('ult','max','v'),lambda:r.set('max','v'))
    r.if_(('eq','max',0),lambda:r.ret(0))
    r.set('distance',invoke('mesh_normalize',word('@__gfx_light_delta'),word('@__gfx_light_delta+2'),word('@__gfx_light_delta+4'),'@__gfx_light_unit'))
    r.if_(('uge','distance','radius'),lambda:r.ret(0))
    r.expr(sub('radius','distance'));r.emit('ex de,hl');r.expr('intensity');r.emit('ld b,h','ld c,l','call __gfx_wf_umul','push hl');r.expr('radius');r.emit('ld b,h','ld c,l','pop hl','call __gfx_wf_udiv');r._store('intensity')
    r.set('v',invoke('mesh_dot','@__gfx_light_normal','@__gfx_light_unit',2))
    r.if_(('negative','v'),lambda:r.ret(0));r.if_(('slt',256,'v'),lambda:r.set('v',256))
    r.ret(invoke('mesh_mul','v','intensity'));out.append(r.finish())
    r=NativeRoutine('light_faces',('face',),('i','p','sum','v'))
    r.if_(('eq',word('@__gfx_light_count'),0),lambda:r.ret(0))
    def normal():
        for axis in range(3):r.write(add('@__gfx_light_normal',axis*2),invoke('mesh_dot',add('@__gfx_mesh_rotation',axis*6),add('face',normal_offset),2))
    def identity_normal():
        r.expr(add('face',normal_offset));r.emit('ld de,__gfx_light_normal','ld bc,6','ldir')
    r.if_(('ne',word('@__gfx_mesh_identity_active'),0),identity_normal,normal)
    if points:r.if_(('ne',word('@__gfx_light_points'),0),lambda:r.expr(invoke('light_centroid','face')))
    r.set('i',0);r.set('p','@__gfx_light_records');r.set('sum',0)
    def light():
        def directional():
            r.set('v',invoke('mesh_dot','@__gfx_light_normal',add('p',2),2))
            r.if_(('negative','v'),lambda:r.set('v',0));r.if_(('slt',256,'v'),lambda:r.set('v',256))
            r.set('v',invoke('mesh_mul','v',word(add('p',8))))
        if points:r.if_(('eq',word('p'),1),directional,lambda:r.set('v',invoke('light_point','p')))
        else:directional()
        r.set('sum',add('sum','v'));r.set('p',add('p',12));r.set('i',add('i',1))
    r.while_(('ult','i',word('@__gfx_light_count')),light);r.ret('sum');out.append(r.finish())
    return out
