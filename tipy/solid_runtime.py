"""Native clipped triangles, flat lighting, 4x4 dithering and 8-bit inverse Z.

Only solid meshes link this path and its 6144-byte depth buffer. No host draw,
grayscale framebuffer, temporal flicker, or per-frame allocation is involved.
"""
from .camera_math import NativeRoutine,invoke
from .shape_runtime import slot,words
from .list_runtime import add,sub,word,byte


def _copy(r,src,dst,count=12):
    r.expr(src);r.emit('push hl');r.expr(dst);r.emit('ex de,hl','pop hl',f'ld bc,{count}','ldir')


def routines(extended=False,culling=True,depth_enabled=True,dynamic_depth=False,quads=False,blank_spans=False,fast_spans=True):
    out=['__gfx_solid_guard_before: db 165\n__gfx_solid_depth: defs 6144,0\n__gfx_solid_guard_after: db 90','__gfx_solid_flags: defs 64,0',
         '__gfx_solid_light_vector: dw -68,137,205\n__gfx_solid_ambient: dw 3\n__gfx_solid_direction_enabled: dw 1',
         '''__gfx_solid_poly_a: defs 144,0
__gfx_solid_poly_b: defs 144,0
__gfx_solid_hits: defs 12,0
__gfx_solid_edges: defs 42,0
__gfx_solid_normal: defs 6,0
__gfx_solid_local_light: defs 6,0
__gfx_solid_pattern: db 0''']
    bayer=((0,8,2,10),(12,4,14,6),(3,11,1,9),(15,7,13,5))
    patterns=[sum((128>>x) for x in range(8) if bayer[y][x%4]>=level) for level in range(17) for y in range(4)]
    out.append('__gfx_solid_patterns: db '+','.join(map(str,patterns)))
    out.append('''__gfx_solid_clear:
        ld hl,__gfx_solid_depth
        ld de,__gfx_solid_depth+1
        ld bc,6143
        ld (hl),0
        ldir
        ret''')
    r=NativeRoutine('solid_light',('x','y','z','ambient'))
    r.write('@__gfx_solid_direction_enabled',1);r.write('@__gfx_light_count',0);r.write('@__gfx_light_points',0)
    r.if_(('slt','ambient',0),lambda:r.fail(14));r.if_(('slt',16,'ambient'),lambda:r.fail(14))
    for n in 'xyz':
        r.if_(('slt',n,-2047),lambda:r.fail(14));r.if_(('slt',2047,n),lambda:r.fail(14))
    r.expr(invoke('mesh_normalize','x','y','z','@__gfx_solid_light_vector'));r.write('@__gfx_solid_ambient','ambient');r.ret();out.append(r.finish())

    r=NativeRoutine('solid_mul32',('a','b','out'),('negative',)+words('p',2))
    r.set('negative',0)
    for n in ('a','b'):
        r.if_(('negative',n),lambda n=n:(r.set(n,sub(0,n)),r.set('negative',sub(1,'negative'))))
    r.expr('a');r.emit('ex de,hl');r.expr('b');r.emit('ld b,h','ld c,l','call __gfx_wf_umul');r._store('p0');r.emit('ex de,hl');r._store('p1')
    r.if_(('ne','negative',0),lambda:r.negate('p'));r.save('p','out');r.ret();out.append(r.finish())
    r=NativeRoutine('solid_facing',('normal','point'),('i',)+words('sum',2)+words('p',2))
    r.zero('sum');r.set('i',0)
    def facing():
        r.expr(invoke('solid_mul32',word(add('normal','i')),word(add('point','i')),slot('p0')))
        r.binary('sum','sum','p');r.set('i',add('i',2))
    r.while_(('ult','i',6),facing);r.if_(('negative','sum1'),lambda:r.ret(1));r.ret(0);out.append(r.finish())

    r=NativeRoutine('solid_lerpq',('a','b','n','d'),('negative',))
    r.set('negative',0)
    r.if_(('ult','b','a'),lambda:(r.set('negative',1),r.set('b',sub('a','b'))),lambda:r.set('b',sub('b','a')))
    r.expr('b');r.emit('ex de,hl');r.expr('n');r.emit('ld b,h','ld c,l','call __gfx_wf_umul','push hl');r.expr('d');r.emit('ld b,h','ld c,l','pop hl','call __gfx_wf_udiv');r._store('b')
    r.if_(('ne','negative',0),lambda:r.ret(sub('a','b')));r.ret(add('a','b'));out.append(r.finish())

    r=NativeRoutine('solid_cut',('outside','inside','out','axis','bound'),('x','y','n','d','other'))
    if not depth_enabled:
        wide,positive,forward,denominator=r.label(),r.label(),r.label(),r.label()
        r.expr('axis');r.emit('ld a,l','cp 4',f'jp z,{wide}')
        r.expr(word(add('outside','axis')));r._store('x')
        r.emit(f"ld e,(ix{r.slots['bound']:+d})",f"ld d,(ix{r.slots['bound']+1:+d})",'ld a,h','xor 128','ld h,a','ld a,d','xor 128','ld d,a','or a','sbc hl,de','push af',f'jr nc,{positive}',
            'xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a');r.mark(positive);r._store('n')
        r.expr(word(add('inside','axis')));r.emit(f"ld e,(ix{r.slots['x']:+d})",f"ld d,(ix{r.slots['x']+1:+d})",'or a','sbc hl,de','pop af',f'jr c,{forward}',
            'xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a');r.mark(forward);r._store('d')
        r.set('other',sub(14,'axis'))
        r.write(add('out','other'),invoke('wf_lerp',word(add('outside','other')),word(add('inside','other')),'n','d'))
        r.write(add('out','axis'),'bound');r.ret();r.mark(wide)
    _copy(r,'outside','out')
    r.set('x',word(add('outside','axis')));r.set('y',word(add('inside','axis')))
    r.if_(('slt','x','y'),lambda:(r.set('n',sub('bound','x')),r.set('d',sub('y','x'))),lambda:(r.set('n',sub('x','bound')),r.set('d',sub('x','y'))))
    def interpolate(offset,q=False):r.write(add('out',offset),invoke('solid_lerpq' if q else 'wf_lerp',word(add('outside',offset)),word(add('inside',offset)),'n','d'))
    def screen_cut():
        r.set('other',sub(14,'axis'));interpolate('other')
        if depth_enabled:interpolate(10,True)
    r.if_(('eq','axis',4),lambda:(interpolate(0),interpolate(2)),screen_cut)
    r.write(add('out','axis'),'bound');r.ret();out.append(r.finish())

    out.append(_clip())

    out.extend(_edge_dda())
    r=NativeRoutine('solid_project',('p',),('flags','v'))
    r.expr(invoke('wf_project','p'));r.set('flags',word(add('p',10)))
    r.expr('flags');r.emit('ld a,l','and 3','ld l,a','ld h,0');r._store('v')
    def depth():
        r.expr(word('@__gfx_mesh_near'));r.emit('ex de,hl','ld bc,65280','call __gfx_wf_umul','push hl','push de');r.expr(word(add('p',4)));r.emit('ld b,h','ld c,l','pop de','pop hl','call __gfx_wf_udiv');r._store('v');r.write(add('p',10),'v')
    if depth_enabled:r.if_(('eq','v',0),depth)
    r.ret('flags');out.append(r.finish())
    r=NativeRoutine('solid_prepare',('count',),('i','p'))
    r.set('i',0);r.set('p','@__gfx_wf_records')
    def prepare():
        r.write(add('@__gfx_solid_flags','i'),invoke('solid_project','p'),1)
        r.set('p',add('p',12));r.set('i',add('i',1))
    r.while_(('ult','i','count'),prepare);r.ret();out.append(r.finish())
    out.append(_span() if depth_enabled else _flat_span())

    r=NativeRoutine('solid_triangle',('a','b','c','shade'),('y','end','v','n','out','left','right'))
    for i,(a,b) in enumerate((('a','b'),('b','c'),('c','a'))):
        r.expr(invoke('solid_edge_setup',a,b,add('@__gfx_solid_edges',i*14)))
    r.set('y',word(add('a',8)));r.set('end','y')
    for p in ('b','c'):
        r.set('v',word(add(p,8)));r.if_(('slt','v','y'),lambda:r.set('y','v'));r.if_(('slt','end','v'),lambda:r.set('end','v'))
    def row():
        r.set('n',0);r.set('out','@__gfx_solid_hits')
        for i in range(3):
            r.if_(('ne',invoke('solid_edge_read',add('@__gfx_solid_edges',i*14),'y','out'),0),lambda:(r.set('n',add('n',1)),r.set('out',add('out',4))))
        def span():
            r.set('left','@__gfx_solid_hits');r.set('right','@__gfx_solid_hits+4')
            r.if_(('ult',word('right'),word('left')),lambda:(r.set('v','left'),r.set('left','right'),r.set('right','v')))
            r.expr(invoke('solid_span',word('left'),word(add('left',2)),word('right'),word(add('right',2)),'y','shade'))
        r.if_(('eq','n',2),span);r.set('y',add('y',1))
    r.while_(('ult','y','end'),row);r.ret();out.append(r.finish())
    if not depth_enabled or dynamic_depth:
        out.extend(['__gfx_solid_chain_x: defs 64,0',_fast_span() if fast_spans else _basic_span(),_flat_polygon(blank_spans=blank_spans)])
    out.append(_draw(extended,culling,depth_enabled,dynamic_depth,quads,blank_spans))
    if not depth_enabled:
        out[0]=''
        out=[('__gfx_solid_clear:\n        ret' if s.startswith('__gfx_solid_clear:') else s) for s in out]
    if dynamic_depth:
        out=[s.replace('__gfx_solid_span:\n','__gfx_solid_span:\n        ld a,(__gfx_solid_depth_enabled)\n        or a\n        jp z,__gfx_solid_flat_span\n') for s in out]
        out.extend(['__gfx_solid_depth_enabled: dw 1',_flat_span().replace('__gfx_solid_span','__gfx_solid_flat_span')])
    return out


def _clip():
    """Sutherland-Hodgman with register-held cursor and byte predicates."""
    r=NativeRoutine('solid_clip',('src','count','dst','axis','bound','keep'),
                    ('prev','ip','ic','out','n','outside','inside'))
    r.if_(('eq','count',0),lambda:r.ret(0))
    r.emit('push iy');r.expr('src');r.emit('push hl','pop iy')
    r.expr('count');r.emit('dec hl','add hl,hl','add hl,hl','ld d,h','ld e,l','add hl,hl','add hl,de','push hl')
    r.expr('src');r.emit('pop de','add hl,de');r._store('prev')
    r.set('out','dst');r.set('n',0)
    def value(point):
        if point=='current':r.emit('push iy','pop hl')
        else:r.expr(point)
        r.emit(f"ld e,(ix{r.slots['axis']:+d})",f"ld d,(ix{r.slots['axis']+1:+d})",'add hl,de',
               'ld e,(hl)','inc hl','ld h,(hl)','ld l,e',
               f"ld e,(ix{r.slots['bound']:+d})",f"ld d,(ix{r.slots['bound']+1:+d})")
    def inside(point):
        equal,done=r.label(),r.label();value(point)
        r.emit('ld a,h','xor 128','ld h,a','ld a,d','xor 128','ld d,a','or a','sbc hl,de',f'jr z,{equal}',
               'ld a,0','adc a,0','ld c,a',f"ld a,(ix{r.slots['keep']:+d})",'xor c',f'jr {done}')
        r.mark(equal);r.emit('ld a,1');r.mark(done)
    inside('prev');r.emit(f"ld (ix{r.slots['ip']:+d}),a",f"ld b,(ix{r.slots['count']:+d})")
    loop,no_cross,selected,no_append=(r.label() for _ in range(4));r.mark(loop);r.emit('push bc')
    inside('current');r.emit(f"ld (ix{r.slots['ic']:+d}),a",'ld c,a',f"ld a,(ix{r.slots['ip']:+d})",'xor c',f'jp z,{no_cross}')
    r.set('outside','prev');r.emit('push iy','pop hl');r._store('inside')
    r.emit(f"ld a,(ix{r.slots['ip']:+d})",'or a',f'jr z,{selected}')
    r.set('inside','prev');r.emit('push iy','pop hl');r._store('outside');r.mark(selected)
    value('inside');r.emit('or a','sbc hl,de',f'jp z,{no_cross}')
    for argument in ('outside','inside','out','axis','bound'):r.expr(argument);r.emit('push hl')
    r.emit('call __gfx_solid_cut','ld hl,10','add hl,sp','ld sp,hl')
    r.expr('out');r.emit('ld de,12','add hl,de');r._store('out');r.emit(f"inc (ix{r.slots['n']:+d})")
    r.mark(no_cross);r.emit(f"ld a,(ix{r.slots['ic']:+d})",'or a',f'jr z,{no_append}','push iy','pop hl',
        f"ld e,(ix{r.slots['out']:+d})",f"ld d,(ix{r.slots['out']+1:+d})",'ld bc,12','ldir',
        f"ld (ix{r.slots['out']:+d}),e",f"ld (ix{r.slots['out']+1:+d}),d",f"inc (ix{r.slots['n']:+d})")
    r.mark(no_append);r.emit('push iy','pop hl');r._store('prev')
    r.emit(f"ld a,(ix{r.slots['ic']:+d})",f"ld (ix{r.slots['ip']:+d}),a",'ld de,12','add iy,de','pop bc','dec b',f'jp nz,{loop}','pop iy')
    r.ret('n');return r.finish()


def _flat_span():
    """Stack ABI wrapper plus a register-only opaque scanline kernel."""
    r=NativeRoutine('solid_span',('lx','lq','rx','rq','y','shade'))
    for value,register in (('lx','b'),('rx','c')):
        r.expr(add(value,127));r.emit(f'ld {register},h')
    r.expr('y');r.emit('ld d,l');r.expr('shade');r.emit('ld e,l','call __gfx_solid_span_row')
    r.ret()
    return r.finish()+"""
; B=start, C=end (exclusive), D=y, E=shade. IX/IY preserved.
__gfx_solid_span_row:
        ld a,c
        sub b
        ret z
        ret c
        push bc
        push de
        ld a,d
        and 3
        ld l,a
        ld h,0
        ld a,e
        add a,a
        add a,a
        ld e,a
        ld d,0
        add hl,de
        ld de,__gfx_solid_patterns
        add hl,de
        ld a,(hl)
        ld (__gfx_solid_pattern),a
        pop de
        pop bc
        ld l,d
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld a,b
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
        ld de,0x9340
        add hl,de
        push hl
        ld a,c
        dec a
        and 7
        ld l,a
        ld h,0
        ld de,__gfx_solid_span_last_masks
        add hl,de
        ld a,(hl)
        push af
        ld a,b
        and 7
        ld l,a
        ld h,0
        ld de,__gfx_solid_span_first_masks
        add hl,de
        ld d,(hl)
        pop af
        ld e,a
        ld a,c
        dec a
        srl a
        srl a
        srl a
        ld c,a
        ld a,b
        srl a
        srl a
        srl a
        ld b,a
        ld a,c
        sub b
        ld b,a
        ld a,(__gfx_solid_pattern)
        ld c,a
        pop hl
        ld a,b
        or a
        jr nz,__gfx_solid_span_multiple
        ld a,d
        and e
        ld d,a
        jr __gfx_solid_span_blend
__gfx_solid_span_multiple:
        ld a,c
        xor (hl)
        and d
        xor (hl)
        ld (hl),a
        inc hl
        dec b
        jr z,__gfx_solid_span_last
__gfx_solid_span_middle:
        ld (hl),c
        inc hl
        djnz __gfx_solid_span_middle
__gfx_solid_span_last:
        ld d,e
__gfx_solid_span_blend:
        ld a,c
        xor (hl)
        and d
        xor (hl)
        ld (hl),a
        ret
__gfx_solid_span_first_masks: db 255,127,63,31,15,7,3,1
__gfx_solid_span_last_masks: db 128,192,224,240,248,252,254,255

"""


def _basic_span():
    source=_flat_span()
    start=source.index('        push hl\n        ld a,c\n        dec a')
    end=source.index('        ld a,(__gfx_solid_pattern)',start)
    tail=source[end:].replace('        ld a,(__gfx_solid_pattern)','        ex af,af\'')
    prefix="""__gfx_solid_fast_span:
        ld a,c
        sub b
        ret z
        ret c
        ld a,d
        ex af,af'
        ld a,b
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
"""
    return (prefix+source[start:end]+tail).replace('__gfx_solid_span_','__gfx_solid_fast_span_')


def _fast_span():
    # Two aligned pages map x directly to (mask, byte index), removing all
    # per-row shifts and variable pointer arithmetic from span setup.
    source=_basic_span();tail=source[source.index('        ld a,b\n        or a'):source.index('__gfx_solid_fast_span_first_masks:')]
    prefix="""__gfx_solid_fast_span:
        ld a,c
        sub b
        ret z
        ret c
        ld a,d
        ex af,af'
        push hl
        ld a,b
        add a,a
        ld l,a
        ld h,__gfx_solid_fast_span_tables >> 8
        ld d,(hl)
        inc hl
        ld b,(hl)
        ld a,c
        add a,a
        ld l,a
        inc h
        ld e,(hl)
        inc hl
        ld c,(hl)
        ld a,c
        sub b
        ld c,a
        pop hl
        ld a,l
        add a,b
        ld l,a
        jr nc,__gfx_solid_fast_span_address
        inc h
__gfx_solid_fast_span_address:
        ld b,c
        ex af,af'
        ld c,a
"""
    first=[];last=[]
    for x in range(128):
        first.extend((255>>(x&7),x>>3))
        last.extend(((255<<(7-((x-1)&7)))&255,((x-1)&255)>>3))
    return prefix+tail+'__gfx_solid_fast_span_tables: db '+','.join(map(str,first+last))


def _edge_dda():
    r=NativeRoutine('solid_edge_setup',('a','b','out'),('ya','yb','d','v','x0','x1','q0','q1','negative'))
    r.set('ya',word(add('a',8)));r.set('yb',word(add('b',8)))
    r.if_(('slt','yb','ya'),lambda:(r.set('v','a'),r.set('a','b'),r.set('b','v'),r.set('v','ya'),r.set('ya','yb'),r.set('yb','v')))
    r.write('out','ya');r.write(add('out',2),'yb');r.if_(('eq','ya','yb'),lambda:r.ret())
    r.set('d',sub('yb','ya'))
    for n,p in (('x0','a'),('x1','b')):
        r.expr(word(add(p,6)));r.emit('ld h,l','ld l,0');r._store(n)
    r.write(add('out',4),invoke('wf_lerp','x0','x1',1,add('d','d')))
    r.set('q0',word(add('a',10)));r.set('q1',word(add('b',10)))
    r.write(add('out',6),invoke('solid_lerpq','q0','q1',1,add('d','d')))
    for x,y,offset,unsigned in (('x0','x1',8,False),('q0','q1',10,True)):
        r.set('negative',0)
        r.if_(('ult' if unsigned else 'slt',y,x),lambda x=x,y=y:(r.set('negative',1),r.set('v',sub(x,y))),lambda x=x,y=y:r.set('v',sub(y,x)))
        r.expr('d');r.emit('ld b,h','ld c,l');r.expr('v');r.emit('ld de,0','call __gfx_wf_udiv');r._store('v')
        if unsigned:r.write(add('out',12),'negative')
        else:r.if_(('ne','negative',0),lambda:r.set('v',sub(0,'v')))
        r.write(add('out',offset),'v')
    r.ret()
    setup=r.finish()
    r=NativeRoutine('solid_edge_read',('edge','y','out'),('v',))
    r.if_(('slt','y',word('edge')),lambda:r.ret(0));r.if_(('sge','y',word(add('edge',2))),lambda:r.ret(0))
    _copy(r,add('edge',4),'out',4)
    r.write(add('edge',4),add(word(add('edge',4)),word(add('edge',8))))
    r.set('v',word(add('edge',6)))
    r.if_(('ne',word(add('edge',12)),0),lambda:r.set('v',sub('v',word(add('edge',10)))),lambda:r.set('v',add('v',word(add('edge',10)))))
    r.write(add('edge',6),'v');r.ret(1)
    return [setup,r.finish()]


def _span():
    r=NativeRoutine('solid_span',('lx','lq','rx','rq','y','shade'),('start','end','q','step','negative','n','d','v'))
    for name,x in (('start','lx'),('end','rx')):
        r.expr(add(x,127));r.emit('ld l,h','ld h,0');r._store(name)
    r.if_(('uge','start','end'),lambda:r.ret())
    r.set('d',sub('rx','lx'));r.expr('start');r.emit('ld h,l','ld l,128');r._store('v');r.set('n',sub('v','lx'))
    r.set('q',invoke('solid_lerpq','lq','rq','n','d'));r.set('negative',0)
    r.if_(('ult','rq','lq'),lambda:(r.set('negative',1),r.set('v',sub('lq','rq'))),lambda:r.set('v',sub('rq','lq')))
    r.expr('v');r.emit('ld d,h','ld e,l','ld bc,256','call __gfx_wf_umul','push hl');r.expr('d');r.emit('ld b,h','ld c,l','pop hl','call __gfx_wf_udiv');r._store('step')
    r.expr('y');r.emit('ld a,l','and 3','ld c,a','ld b,0');r.expr('shade');r.emit('add hl,hl','add hl,hl','add hl,bc','ld de,__gfx_solid_patterns','add hl,de','ld a,(hl)','ld (__gfx_solid_pattern),a')
    r.code.extend(_span_setup(r))
    for direction in (0,1):
        name=f'__gfx_solid_span_{direction}'
        r.mark(name)
        r.emit('ld a,(ix+0)','cp h',f'jr c,{name}_new',f'jr nz,{name}_next','ld a,(__gfx_solid_pattern)','and c',f'jr z,{name}_next',f'jr {name}_black')
        r.mark(name+'_new');r.emit('ld (ix+0),h','ld a,(__gfx_solid_pattern)','and c',f'jr nz,{name}_black','ld a,c','cpl','and (iy+0)',f'jr {name}_store')
        r.mark(name+'_black');r.emit('ld a,(iy+0)','or c');r.mark(name+'_store');r.emit('ld (iy+0),a')
        r.mark(name+'_next');r.emit('inc ix','rrc c',f'jr nc,{name}_same','inc iy');r.mark(name+'_same')
        r.emit(*(['or a','sbc hl,de'] if direction else ['add hl,de']),f'djnz {name}','pop ix','pop iy',f'jp {r.name}_return')
    return r.finish()


def _span_setup(r):
    # Generate setup in a temporary emitter with the same frame layout.
    t=NativeRoutine('solid_span_setup');t.slots=r.slots;t.code=[]
    t.emit('push iy')
    t.expr('y');t.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','push hl');t.expr('start');t.emit('srl l','srl l','srl l','pop de','add hl,de','ld de,0x9340','add hl,de','push hl','pop iy')
    t.expr('y');t.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','add hl,hl','add hl,hl','add hl,hl','push hl');t.expr('start');t.emit('pop de','add hl,de','ld de,__gfx_solid_depth','add hl,de','push hl')
    t.expr('start');t.emit('ld a,l','and 7','ld b,a','ld a,128','inc b','jr __gfx_solid_span_mask_ready');t.mark('__gfx_solid_span_mask_shift');t.emit('rrca');t.mark('__gfx_solid_span_mask_ready');t.emit('djnz __gfx_solid_span_mask_shift','ld c,a')
    t.expr(sub('end','start'));t.emit('ld b,l');t.expr('negative');t.emit('ld a,l','or a','pop hl','push ix','push hl');t.expr('step');t.emit('ex de,hl');t.expr('q');t.emit('pop ix','jp nz,__gfx_solid_span_1','jp __gfx_solid_span_0')
    return t.code


def _flat_polygon(row_name='solid_span_row',blank_spans=False):
    """Walk the two monotone chains of a convex clipped polygon.

    The first chain stores one intersection per row; the second emits spans
    directly. No per-row min/max initialization or second bounds pass.
    Scanline coherence: graphics.stanford.edu/courses/cs248-08/scan/scan1.html
    """
    r=NativeRoutine('solid_flat_polygon',('poly','count','shade'),
                    ('first','last','top_point','bottom_point','top','bottom','p','next','stride','stride_mask'))
    r.set('stride',1)
    if blank_spans:
        ordinary,twice=r.label(),r.label();r.emit('ld a,(__gfx_solid_transparent_white)','or a',f'jr z,{ordinary}')
        r.expr('shade');r.emit('ld a,l','cp 12',f'jr c,{ordinary}','cp 15',f'jr c,{twice}')
        r.set('stride',4);r.emit(f'jr {ordinary}');r.mark(twice);r.set('stride',2);r.mark(ordinary)
    r.expr(sub('stride',1));r._store('stride_mask')
    r.set('top_point',0);r.set('bottom_point',0)
    extent,next_top,next_bottom,advance=(r.label() for _ in range(4))
    r.expr('count');r.emit('ld b,l','ld c,0');r.expr('poly');r.emit('ld de,8','add hl,de','ld d,64','ld e,0')
    r.mark(extent);r.emit('ld a,(hl)','cp d',f'jr nc,{next_top}','ld d,a',f"ld (ix{r.slots['top_point']:+d}),c");r.mark(next_top)
    r.emit('cp e',f'jr c,{next_bottom}',f'jr z,{next_bottom}','ld e,a',f"ld (ix{r.slots['bottom_point']:+d}),c");r.mark(next_bottom)
    r.emit('ld a,l','add a,12','ld l,a',f'jr nc,{advance}','inc h');r.mark(advance);r.emit('inc c',f'djnz {extent}')
    r.emit('ld l,d','ld h,0');r._store('top');r.emit('ld l,e');r._store('bottom')
    r.if_(('uge','top','bottom'),lambda:r.ret())
    for point in ('top_point','bottom_point'):
        r.expr(point);r.emit('add hl,hl','add hl,hl','ld d,h','ld e,l','add hl,hl','add hl,de','push hl');r.expr('poly');r.emit('pop de','add hl,de');r._store(point)
    r.set('first','poly')
    r.expr('count');r.emit('dec hl','add hl,hl','add hl,hl','ld d,h','ld e,l','add hl,hl','add hl,de','push hl');r.expr('poly');r.emit('pop de','add hl,de');r._store('last')
    # Both chains are monotone after viewport clipping: y and x endpoints
    # fit in bytes. Only the interpolated x coordinate needs Q8 precision.
    for chain in range(2):
        r.set('p','top_point')
        edge,wrap,selected,horizontal,nonzero,positive,stepped,done=(r.label() for _ in range(8))
        r.mark(edge)
        r.expr('p');r.emit('push hl');r.expr('first' if chain else 'last');r.emit('pop de','or a','sbc hl,de',f'jr z,{wrap}',
            'ex de,hl',f'ld de,{-12 if chain else 12}','add hl,de',f'jr {selected}')
        r.mark(wrap);r.expr('last' if chain else 'first');r.mark(selected);r._store('next')
        r.emit('push iy');r.expr('p');r.emit('push hl','pop iy')
        r.expr('next');r.emit('ld de,8','add hl,de','ld a,(hl)','ld c,a','ld a,(iy+8)','ld b,a','ld a,c','sub b',f'jp z,{horizontal}')
        r.emit('ld b,0','ld c,a')
        r.expr('next');r.emit('ld de,6','add hl,de','ld a,(hl)','sub (iy+6)','push af','push bc')
        # Preserve the slope sign while dividing its unsigned magnitude.
        r.emit(f'jr nc,{positive}','neg');r.mark(positive)
        r.emit('ld h,a','ld l,0','ld de,0','pop bc','ld a,h','or a',f'jr z,{stepped}','call __gfx_wf_udiv')
        r.mark(stepped);r.emit('pop af',f'jr nc,{nonzero}','xor a','sub l','ld l,a','sbc a,a','sub h','ld h,a');r.mark(nonzero)
        r.emit('ld d,h','ld e,l','sra h','rr l','ld a,(iy+6)','add a,h','ld h,a','push hl','push de',
            'ld c,(iy+8)')
        r.expr('next');r.emit('ld de,8','add hl,de','ld a,(hl)','sub c','ld b,a','ld l,c','ld h,0','ld de,__gfx_solid_chain_x','add hl,de','push hl','pop iy','pop de','pop hl')
        if blank_spans:
            aligned,align,ready=r.label(),r.label(),r.label()
            r.mark(align);r.emit('ld a,c',f"and (ix{r.slots['stride_mask']:+d})",f"cp (ix{r.slots['stride_mask']:+d})",f'jr z,{aligned}',
                'add hl,de','inc c','inc iy','dec b',f'jp z,{horizontal}',f'jr {align}')
            r.mark(aligned);r.emit(f"ld a,(ix{r.slots['stride']:+d})",'cp 1',f'jr z,{ready}','sla e','rl d','cp 2')
            four=r.label();r.emit(f'jr nz,{four}','inc b','srl b',f'jr {ready}');r.mark(four)
            r.emit('sla e','rl d','ld a,b','add a,3','srl a','srl a','ld b,a');r.mark(ready)
        store,fill,swap,ordered,finish=(r.label() for _ in range(5))
        if chain==0:
            if blank_spans:
                r.emit('push hl','push de','push bc');r.expr('stride');r.emit('push hl','exx','pop de','exx','pop bc','pop de','pop hl')
            r.mark(store);r.emit('ld a,l','add a,127','ld a,h','adc a,0','ld (iy+0),a')
            if blank_spans:r.emit('exx','add iy,de','exx')
            else:r.emit('inc iy')
            r.emit('add hl,de',f'djnz {store}')
        else:
            # Keep the framebuffer row in the alternate register bank and
            # pin four Bayer rows in IX for this edge. No per-row address or
            # shade lookup remains in the scanline kernel.
            r.emit('push ix','push hl','push de','push bc','ld l,c','ld h,0','add hl,hl','add hl,hl','ld d,h','ld e,l','add hl,hl','add hl,de','ld de,0x9340','add hl,de','exx')
            r.expr('shade');r.emit('add hl,hl','add hl,hl','ld de,__gfx_solid_patterns','add hl,de','push hl','pop ix','pop bc','pop de','pop hl')
            phases=[r.label() for _ in range(4)];two=[r.label(),r.label()];four=r.label();finish=r.label()
            if blank_spans:
                # IX now addresses patterns. Read the saved stride from the
                # frame pointer held on the stack, before selecting row loops.
                r.emit('push hl','push de','push ix','ld hl,6','add hl,sp','ld e,(hl)','inc hl','ld d,(hl)','push de','pop ix',f"ld a,(ix{r.slots['stride']:+d})",'pop ix','pop de','pop hl','cp 4',f'jp z,{four}','cp 2')
                choose=r.label();r.emit(f'jr nz,{choose}','ld a,c','and 2',f'jp z,{two[0]}',f'jp {two[1]}');r.mark(choose)
            r.emit('ld a,c','and 3',f'jp z,{phases[0]}','dec a',f'jp z,{phases[1]}','dec a',f'jp z,{phases[2]}',f'jp {phases[3]}')
            variants=[(phases,list(range(4)),1)]
            if blank_spans:variants.extend([(two,[1,3],2),([four],[3],4)])
            for labels,patterns,stride in variants:
                for phase,(label,pattern) in enumerate(zip(labels,patterns)):
                    r.mark(label)
                    r.emit('ld a,l','add a,127','ld a,h','adc a,0','exx','ld c,a','ld b,(iy+0)','ld a,b','cp c')
                    ordered=r.label();r.emit(f'jr c,{ordered}',f'jr z,{ordered}','ld a,b','ld b,c','ld c,a');r.mark(ordered)
                    r.emit(f'ld d,(ix+{pattern})','push hl','call __gfx_solid_fast_span','pop hl',f'ld de,{12*stride}','add hl,de','exx','add hl,de')
                    for _ in range(stride):r.emit('inc iy')
                    r.emit('dec b',f'jp z,{finish}')
                    if phase==len(labels)-1:r.emit(f'jp {labels[0]}')
            r.mark(finish);r.emit('pop ix')
        r.mark(horizontal);r.emit('pop iy');r.set('p','next')
        r.expr('bottom_point');r.emit('push hl');r.expr('p');r.emit('pop de','or a','sbc hl,de',f'jp nz,{edge}')
    r.ret();return r.finish()



def _draw(extended,culling,depth_enabled=True,dynamic_depth=False,quads=False,blank_spans=False):
    normal_offset=6 if quads else 4
    r=NativeRoutine('solid_draw',('faces','count','cull','shaded'),('i','face','j','p','n','flags','common','v','shade','poly','other'))
    r.if_(('uge','count',129),lambda:r.fail(14))
    def local_light():
        for i in range(3):r.write(add('@__gfx_solid_local_light',i*2),invoke('mesh_dot','@__gfx_solid_light_vector',add('@__gfx_mesh_rotation',i*2),6))
    r.if_(('ne','shaded',0),lambda:r.if_(('ne',word('@__gfx_solid_direction_enabled'),0),local_light))
    r.set('face','faces');r.set('i',0)
    def face():
        skip=r.label()
        r.set('n',3)
        r.emit('push iy');r.expr('face');r.emit('push hl','pop iy','ld b,63','ld c,0')
        def flags(j):
            r.emit(f'ld l,(iy+{j})','ld h,0','ld de,__gfx_solid_flags','add hl,de','ld a,(hl)','or c','ld c,a','ld a,(hl)','and b','ld b,a')
        for j in range(3):flags(j)
        if quads:
            triangle=r.label();r.emit('ld a,(iy+3)','cp 255',f'jr z,{triangle}');flags(3);r.emit(f"inc (ix{r.slots['n']:+d})");r.mark(triangle)
        r.emit(f"ld (ix{r.slots['flags']:+d}),c",f"ld (ix{r.slots['flags']+1:+d}),0",'ld a,b','or a')
        visible=r.label();r.emit(f'jr z,{visible}','pop iy',f'jp {skip}');r.mark(visible)
        def vertex(j):
            r.emit(f'ld l,(iy+{j})','ld h,0','add hl,hl','add hl,hl','ld d,h','ld e,l','add hl,hl','add hl,de','ld de,__gfx_wf_records','add hl,de',
                f'ld de,__gfx_solid_poly_a+{j*12}','ld bc,12','ldir')
        for j in range(3):vertex(j)
        if quads:
            triangle=r.label();r.emit('ld a,(iy+3)','cp 255',f'jr z,{triangle}');vertex(3);r.mark(triangle)
        r.emit('pop iy')
        def cull():
            for j in range(3):r.write(add('@__gfx_solid_normal',j*2),invoke('mesh_dot',add('@__gfx_mesh_norm_view',j*6),add('face',normal_offset),2))
            r.if_(('eq',invoke('solid_facing','@__gfx_solid_normal','@__gfx_solid_poly_a'),0),lambda:r.emit(f'jp {skip}'))
        if culling:r.if_(('ne','cull',0),cull)
        r.set('shade',byte(add('face',4 if quads else 3)))
        def light():
            r.set('v',word('@__gfx_solid_ambient'))
            def legacy():
                r.set('v',invoke('mesh_dot',add('face',normal_offset),'@__gfx_solid_local_light',2))
                r.if_(('negative','v'),lambda:r.set('v',0));r.if_(('slt',256,'v'),lambda:r.set('v',256))
                r.set('v',add(word('@__gfx_solid_ambient'),invoke('mesh_mul','v',sub(16,word('@__gfx_solid_ambient')))))
            r.if_(('ne',word('@__gfx_solid_direction_enabled'),0),legacy)
            if extended:r.set('v',add('v',invoke('light_faces','face')))
            r.if_(('slt',16,'v'),lambda:r.set('v',16))
            r.expr(invoke('mesh_imul','v','shade'));r.emit('srl h','rr l','srl h','rr l','srl h','rr l','srl h','rr l');r._store('shade')
        r.if_(('ne','shaded',0),light)
        if blank_spans:
            colored=r.label();r.expr('shade');r.emit('ld a,l','cp 16',f'jr nz,{colored}','ld a,(__gfx_solid_transparent_white)','or a',f'jp nz,{skip}');r.mark(colored)
        r.set('poly','@__gfx_solid_poly_a');r.set('other','@__gfx_solid_poly_b')
        def clip(axis,bound,keep):
            r.set('n',invoke('solid_clip','poly','n','other',axis,bound,keep));r.if_(('ult','n',3),lambda:r.emit(f'jp {skip}'))
            r.set('v','poly');r.set('poly','other');r.set('other','v')
        def depth_clip():
            # A plane whose outcode bit is clear cannot cut this convex face.
            # Frustum/outcode guidance: cubic.org/docs/3dclip.htm
            r.set('common','flags')
            for bound,keep,bit in (('@__gfx_mesh_near',1,0),('@__gfx_mesh_far',0,1)):
                clean=r.label();r.expr('common');r.emit(f'bit {bit},l',f'jp z,{clean}')
                clip(4,word(bound),keep);r.mark(clean)
            r.set('j',0);r.set('p','poly');r.set('flags',0)
            def project():
                r.set('v',invoke('solid_project','p'));r.expr('flags');r.emit('ld b,l');r.expr('v');r.emit('ld a,l','or b','ld l,a','ld h,0');r._store('flags')
                r.set('p',add('p',12));r.set('j',add('j',1))
            r.while_(('ult','j','n'),project)
        r.expr('flags');r.emit('ld a,l','and 3','ld l,a','ld h,0');r._store('v');r.if_(('ne','v',0),depth_clip)
        for axis,bound,keep,bit in ((6,0,1,4),(6,96,0,8),(8,0,1,16),(8,64,0,32)):
            r.expr('flags');r.emit('ld a,l',f'and {bit}','ld l,a','ld h,0');r._store('v');r.if_(('ne','v',0),lambda axis=axis,bound=bound,keep=keep:clip(axis,bound,keep))
        r.set('j',1);r.set('p',add('poly',12))
        def triangle():
            r.expr(invoke('solid_triangle','poly','p',add('p',12),'shade'));r.set('p',add('p',12));r.set('j',add('j',1))
        def fan():r.while_(('ult','j',sub('n',1)),triangle)
        def flat():r.expr(invoke('solid_flat_polygon','poly','n','shade'))
        if not depth_enabled:flat()
        elif dynamic_depth:r.if_(('eq',word('@__gfx_solid_depth_enabled'),0),flat,fan)
        else:fan()
        r.mark(skip);r.set('face',add('face',12 if quads else 10));r.set('i',add('i',1))
    r.while_(('ult','i','count'),face);r.ret();return r.finish()
