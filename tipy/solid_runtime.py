"""Native clipped triangles, flat lighting, 4x4 dithering and 8-bit inverse Z.

Only solid meshes link this path and its 6144-byte depth buffer. No host draw,
grayscale framebuffer, temporal flicker, or per-frame allocation is involved.
"""
from .camera_math import NativeRoutine,invoke
from .shape_runtime import slot,words
from .list_runtime import add,sub,word,byte


def _copy(r,src,dst,count=12):
    r.expr(src);r.emit('push hl');r.expr(dst);r.emit('ex de,hl','pop hl',f'ld bc,{count}','ldir')


def routines(extended=False,culling=True,depth_enabled=True,dynamic_depth=False,quads=False):
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
    _copy(r,'outside','out')
    r.set('x',word(add('outside','axis')));r.set('y',word(add('inside','axis')))
    r.if_(('slt','x','y'),lambda:(r.set('n',sub('bound','x')),r.set('d',sub('y','x'))),lambda:(r.set('n',sub('x','bound')),r.set('d',sub('x','y'))))
    def interpolate(offset,q=False):r.write(add('out',offset),invoke('solid_lerpq' if q else 'wf_lerp',word(add('outside',offset)),word(add('inside',offset)),'n','d'))
    def screen_cut():
        r.set('other',sub(14,'axis'));interpolate('other')
        if depth_enabled:interpolate(10,True)
    r.if_(('eq','axis',4),lambda:(interpolate(0),interpolate(2)),screen_cut)
    r.write(add('out','axis'),'bound');r.ret();out.append(r.finish())

    r=NativeRoutine('solid_clip',('src','count','dst','axis','bound','keep'),('prev','cur','ip','ic','i','n','out','outside','inside'))
    r.if_(('eq','count',0),lambda:r.ret(0))
    r.set('prev',add('src',invoke('mesh_imul',sub('count',1),12)));r.set('cur','src');r.set('out','dst');r.set('i',0);r.set('n',0)
    def inside(point,target):
        equal,done=r.label(),r.label()
        r.expr(word(add(point,'axis')));r.emit('push hl');r.expr('bound')
        r.emit('ex de,hl','pop hl','ld a,h','xor 128','ld h,a','ld a,d','xor 128','ld d,a','or a','sbc hl,de',f'jr z,{equal}',
               'ld a,0','adc a,0','ld b,a',f"ld a,(ix{r.slots['keep']:+d})",'xor b','ld l,a','ld h,0',f'jr {done}')
        r.mark(equal);r.emit('ld hl,1');r.mark(done);r._store(target)
    inside('prev','ip')
    def append(src):
        _copy(r,src,'out');r.set('out',add('out',12));r.set('n',add('n',1))
    def clip():
        inside('cur','ic')
        def intersection():
            r.set('outside','prev');r.set('inside','cur')
            r.if_(('ne','ip',0),lambda:(r.set('outside','cur'),r.set('inside','prev')))
            def cut():
                r.expr(invoke('solid_cut','outside','inside','out','axis','bound'));r.set('out',add('out',12));r.set('n',add('n',1))
            r.if_(('ne',word(add('inside','axis')),'bound'),cut)
        r.if_(('ne','ip','ic'),intersection);r.if_(('ne','ic',0),lambda:append('cur'))
        r.set('prev','cur');r.set('cur',add('cur',12));r.set('ip','ic');r.set('i',add('i',1))
    r.while_(('ult','i','count'),clip);r.ret('n');out.append(r.finish())

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
        out.extend(['__gfx_solid_chain_x: defs 128,0',_flat_polygon('solid_flat_span_row' if depth_enabled else 'solid_span_row')])
    out.append(_draw(extended,culling,depth_enabled,dynamic_depth,quads))
    if not depth_enabled:
        out[0]=''
        out=[('__gfx_solid_clear:\n        ret' if s.startswith('__gfx_solid_clear:') else s) for s in out]
    if dynamic_depth:
        out=[s.replace('__gfx_solid_span:\n','__gfx_solid_span:\n        ld a,(__gfx_solid_depth_enabled)\n        or a\n        jp z,__gfx_solid_flat_span\n') for s in out]
        out.extend(['__gfx_solid_depth_enabled: dw 1',_flat_span().replace('__gfx_solid_span','__gfx_solid_flat_span')])
    return out


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


def _flat_polygon(row_name='solid_span_row'):
    """Walk the two monotone chains of a convex clipped polygon.

    The first chain stores one intersection per row; the second emits spans
    directly. No per-row min/max initialization or second bounds pass.
    Scanline coherence: graphics.stanford.edu/courses/cs248-08/scan/scan1.html
    """
    r=NativeRoutine('solid_flat_polygon',('poly','count','shade'),
                    ('first','last','top_point','bottom_point','top','bottom','p','next','chain','delta','ya','yb','x0','x1','step','negative'))
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
    r.set('first','poly');r.set('last',add('poly',invoke('mesh_imul',sub('count',1),12)));r.set('chain',0)
    def chain():
        r.set('p','top_point');r.set('delta',12)
        r.if_(('ne','chain',0),lambda:r.set('delta',-12))
        def edge():
            r.set('next',add('p','delta'))
            r.if_(('ult','next','first'),lambda:r.set('next','last'))
            r.if_(('ult','last','next'),lambda:r.set('next','first'))
            r.set('ya',word(add('p',8)));r.set('yb',word(add('next',8)))
            def active():
                for n,point in (('x0','p'),('x1','next')):
                    r.expr(word(add(point,6)));r.emit('ld h,l','ld l,0');r._store(n)
                r.set('negative',0)
                r.if_(('ult','x1','x0'),lambda:(r.set('negative',1),r.set('step',sub('x0','x1'))),lambda:r.set('step',sub('x1','x0')))
                r.expr(sub('yb','ya'));r.emit('ld b,h','ld c,l');r.expr('step');r.emit('ld de,0','call __gfx_wf_udiv');r._store('step')
                r.if_(('ne','negative',0),lambda:r.set('step',sub(0,'step')))
                r.expr('ya');r.emit('add hl,hl','ld de,__gfx_solid_chain_x','add hl,de','push iy','push hl','pop iy')
                r.expr('step');r.emit('sra h','rr l','push hl');r.expr('x0');r.emit('pop de','add hl,de','push hl')
                r.expr(sub('yb','ya'));r.emit('ld b,l');r.expr('ya');r.emit('ld c,l');r.expr('step');r.emit('ld d,h','ld e,l','pop hl')
                store,fill,swap,ordered,done=(r.label() for _ in range(5))
                r.emit(f"ld a,(ix{r.slots['chain']:+d})",'or a',f'jr nz,{fill}')
                r.mark(store);r.emit('ld (iy+0),l','ld (iy+1),h','inc iy','inc iy','add hl,de',f'djnz {store}',f'jr {done}')
                r.mark(fill);r.emit('push bc','push de','push hl','ld a,c',"ex af,af'",'ld e,(iy+0)','ld d,(iy+1)',
                    'ld a,h','cp d',f'jr c,{ordered}',f'jr nz,{swap}','ld a,l','cp e',f'jr c,{ordered}')
                r.mark(swap);r.emit('ex de,hl');r.mark(ordered)
                r.emit('ld a,l','add a,127','ld a,h','adc a,0','ld b,a','ld a,e','add a,127','ld a,d','adc a,0','ld c,a',
                    "ex af,af'",'ld d,a',f"ld e,(ix{r.slots['shade']:+d})",f'call __gfx_{row_name}',
                    'pop hl','pop de','pop bc','add hl,de','inc c','inc iy','inc iy',f'djnz {fill}')
                r.mark(done);r.emit('pop iy')
            r.if_(('ult','ya','yb'),active);r.set('p','next')
        r.while_(('ne','p','bottom_point'),edge);r.set('chain',add('chain',1))
    r.while_(('ult','chain',2),chain);r.ret();return r.finish()


def _draw(extended,culling,depth_enabled=True,dynamic_depth=False,quads=False):
    normal_offset=6 if quads else 4
    r=NativeRoutine('solid_draw',('faces','count','cull','shaded'),('i','face','j','p','n','flags','common','v','shade','poly','other'))
    r.if_(('uge','count',129),lambda:r.fail(14))
    def local_light():
        for i in range(3):r.write(add('@__gfx_solid_local_light',i*2),invoke('mesh_dot','@__gfx_solid_light_vector',add('@__gfx_mesh_rotation',i*2),6))
    r.if_(('ne','shaded',0),lambda:r.if_(('ne',word('@__gfx_solid_direction_enabled'),0),local_light))
    r.set('face','faces');r.set('i',0)
    def face():
        skip=r.label()
        r.set('flags',0);r.set('common',63);r.set('n',3)
        def vertex(j):
            r.set('v',byte(add('@__gfx_solid_flags',byte(add('face',j)))))
            for name,op in (('flags','or'),('common','and')):
                r.expr(name);r.emit('ld b,l');r.expr('v');r.emit('ld a,l',op+' b','ld l,a','ld h,0');r._store(name)
            r.expr(byte(add('face',j)));r.emit('add hl,hl','add hl,hl','push hl','add hl,hl','pop de','add hl,de','ld de,__gfx_wf_records','add hl,de');r._store('p')
            _copy(r,'p',add('@__gfx_solid_poly_a',j*12))
        for j in range(3):vertex(j)
        if quads:r.if_(('ne',byte(add('face',3)),255),lambda:(vertex(3),r.set('n',4)))
        r.if_(('ne','common',0),lambda:r.emit(f'jp {skip}'))
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
