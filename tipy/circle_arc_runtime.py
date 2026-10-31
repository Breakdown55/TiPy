"""Adaptive native circles: Bresenham walks or only the visible arc rows/columns."""
from .camera_math import NativeRoutine, invoke
from .shape_runtime import slot, words
from .list_runtime import add, sub, word


def sqrt32():
    r = NativeRoutine('circle_sqrt32', ('lo','hi'), ('n0','n1','rem0','rem1','root','trial','steps'))
    for dst,src in [('n0','lo'),('n1','hi')]: r.set(dst,src)
    for name in ('rem0','rem1','root'):r.set(name,0)
    r.set('steps',16)
    def short():
        r.set('n1','n0');r.set('n0',0);r.set('steps',8)
    r.if_(('eq','hi',0),short)
    def step():
        for _ in range(2):
            for i,name in enumerate(('n0','n1','rem0','rem1')):
                offset=r.slots[name]
                r.emit(f'{"sla" if i==0 else "rl"} (ix{offset:+d})',f'rl (ix{offset+1:+d})')
        r.set('root',add('root','root'));r.set('trial',add(add('root','root'),1))
        def take():
            r.expr('rem0');r.emit('push hl');r.expr('trial');r.emit('ex de,hl','pop hl','or a','sbc hl,de');r._store('rem0')
            r.expr('rem1');r.emit('ld de,0','sbc hl,de');r._store('rem1')
            r.set('root',add('root',1))
        r.if_(('ne','rem1',0),take,lambda:r.if_(('uge','rem0','trial'),take))
        r.set('steps',sub('steps',1))
    r.while_(('ne','steps',0),step);r.ret('root')
    return r.finish()


def extent():
    # Recover the midpoint walk's row extent directly. Rounding sqrt(N)
    # finds the nearest perimeter pixel, while sqrt(N + dy - 1) includes
    # rows emitted by the swapped octant. Their maximum matches the walk.
    r=NativeRoutine('circle_arc_extent',('radius','dy','square0','square1'),('n0','n1','h','nearest','v')+words('product',2))
    r.if_(('ult','radius','dy'),lambda:r.ret(-1))
    r.if_(('eq','dy',0),lambda:r.ret('radius'))
    r.expr(invoke('mul16','dy','dy',slot('product0')))
    r.expr('square0');r.emit('push hl');r.expr('product0');r.emit('ex de,hl','pop hl','or a','sbc hl,de');r._store('n0')
    r.expr('square1');r.emit('push hl');r.expr('product1');r.emit('ex de,hl','pop hl','sbc hl,de');r._store('n1')
    r.set('h',invoke('circle_sqrt32','n0','n1'));r.set('nearest','h')
    r.expr(invoke('mul16','h','h',slot('product0')))
    r.set('v',sub('n0','product0'))
    r.if_(('ult','h','v'),lambda:r.set('nearest',add('nearest',1)))
    r.expr('n0');r.emit('push hl');r.expr(sub('dy',1));r.emit('ex de,hl','pop hl','add hl,de');r._store('n0')
    r.expr('n1');r.emit('ld de,0','adc hl,de');r._store('n1')
    r.set('h',invoke('circle_sqrt32','n0','n1'))
    r.if_(('ult','nearest','h'),lambda:r.ret('h'))
    r.ret('nearest');return r.finish()


def circle(adaptive=True):
    r=NativeRoutine('circle',('cx','cy','radius','filled'),('top','bottom','left','right','y','dy','previous','current','next','inner','vertical','temp')+words('square',2))
    r.if_(('negative','radius'),lambda:r.ret())
    def farthest(center,limit,target):
        r.set(target,center)
        r.if_(('slt',center,0),lambda:r.set(target,sub(limit,center)),
              lambda:r.if_(('slt',center,limit//2+1),lambda:r.set(target,sub(limit,center))))
    farthest('cx',95,'left');farthest('cy',63,'right')
    def inside():
        def cover():
            def fill_screen():
                r.emit('ld a,(__gfx_ink)','or a','ld a,0')
                white=r.label();r.emit(f'jr z,{white}','dec a');r.mark(white)
                r.emit('ld hl,0x9340','ld (hl),a','ld de,0x9341','ld bc,767','ldir')
            r.if_(('ne','filled',0),fill_screen);r.ret()
        # Manhattan distance is a conservative bound on Euclidean distance.
        # Strict interior coverage means an outline contributes no pixels.
        r.if_(('ult',add('left','right'),'radius'),cover)
    r.if_(('uge','radius','left'),lambda:r.if_(('uge','radius','right'),inside))
    r.set('left',invoke('sat_add','cx',sub(0,'radius')));r.set('right',invoke('sat_add','cx','radius'))
    r.if_(('slt','right',0),lambda:r.ret());r.if_(('sge','left',96),lambda:r.ret())
    r.set('top',invoke('sat_add','cy',sub(0,'radius')));r.set('bottom',invoke('sat_add','cy','radius'))
    r.clip_interval('top','bottom',64)
    r.clip_interval('left','right',96)
    if adaptive:
        # Native timings favor Bresenham for short walks, and arcs when the
        # visible strip is small compared with the radius. Both match pixels.
        r.set('temp',sub('bottom','top'))
        r.if_(('ult',sub('right','left'),'temp'),lambda:r.set('temp',sub('right','left')))
        r.set('temp',add('temp',1))
        r.set('inner',add('temp','temp'))
        r.set('previous',add('inner','inner'))
        r.set('current',add('previous','previous'))
        r.if_(('ne','filled',0),lambda:r.set('inner',add('current','previous')),
              lambda:r.set('inner',add(add('current','current'),add('temp','temp'))))
        def walk():
            r.expr(invoke('circle_bresenham','cx','cy','radius','filled'));r.ret()
        r.if_(('ult','radius',8192),lambda:r.if_(('ult','radius','inner'),walk))
    r.set('vertical',0)
    def columns():
        r.set('vertical',1)
        r.set('temp','cx');r.set('cx','cy');r.set('cy','temp')
        r.set('top','left');r.set('bottom','right')
    r.if_(('ult',sub('right','left'),sub('bottom','top')),columns)
    r.expr(invoke('mul16','radius','radius',slot('square0')))
    def lookup(y,target):
        r.set('dy',sub(y,'cy'))
        r.if_(('slt',y,'cy'),lambda:r.set('dy',sub('cy',y)))
        r.set(target,invoke('circle_arc_extent','radius','dy','square0','square1'))
    def span(left,right):
        r.if_(('ne','vertical',0),lambda:r.expr(invoke('styled_v_line','y',left,'y',right)),lambda:r.expr(invoke('styled_h_line',left,'y',right,'y')))
    r.set('y','top');lookup(sub('y',1),'previous');lookup('y','current')
    def row():
        lookup(add('y',1),'next')
        r.set('left',invoke('sat_add','cx',sub(0,'current')));r.set('right',invoke('sat_add','cx','current'))
        def outline():
            r.set('inner','current')
            for neighbour in ('previous','next'):
                r.if_(('slt',neighbour,'inner'),lambda neighbour=neighbour:r.set('inner',neighbour))
            r.if_(('slt','inner','current'),lambda:r.set('inner',add('inner',1)))
            span('left',invoke('sat_add','cx',sub(0,'inner')));span(invoke('sat_add','cx','inner'),'right')
        r.if_(('ne','filled',0),lambda:span('left','right'),outline)
        r.set('previous','current');r.set('current','next');r.set('y',add('y',1))
    r.while_(('slt','y',add('bottom',1)),row)
    r.ret();return r.finish()


def vertical_line():
    from .graphics_runtime import _axis_line
    return _axis_line(True,True).replace('__gfx_v_line','__gfx_styled_v_line')
