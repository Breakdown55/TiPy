"""Native clipped curves and scanline polygons. No host drawing calls.

Ellipse membership uses exact unsigned 64-bit products. Polygon intersections
use unsigned 16x16 multiplication and 32/16 division, including i16 extremes.
"""
from .graphics_runtime import GraphicsRoutine
from .list_runtime import add, sub, word


class ShapeRoutine(GraphicsRoutine):
    def pixel(self,x,y):
        self.expr(x);self.emit('push hl');self.expr(y);self.emit('ex de,hl','pop hl','call __gfx_ink_pixel')
    def expr(self,value):
        if isinstance(value,tuple) and value[0]=="slot":
            self.emit("push ix","pop hl",f"ld de,{self.slots[value[1]]&65535}","add hl,de")
        else:super().expr(value)


def slot(name):return ("slot",name)
def invoke(name,*args):return ("call","__gfx_"+{'line':'styled_line','h_line':'styled_h_line'}.get(name,name),*args)
def words(prefix,count):return tuple(prefix+str(i) for i in reversed(range(count)))


def _multiply(wide,early_exit=False):
    size,steps=(4,32) if wide else (2,16)
    r=ShapeRoutine("mul32" if wide else "mul16",("a0","a1","b0","b1","out") if wide else ("a0","b0","out"),words("m",size)+words("q",2 if wide else 1)+words("v",size))
    for i in range(size):r.set("m"+str(i),"a"+str(i) if i<(2 if wide else 1) else 0);r.set("v"+str(i),0)
    for i in range(2 if wide else 1):r.set("q"+str(i),"b"+str(i))
    loop,skip=r.label(),r.label();done=r.label();r.emit(f"ld b,{steps}");r.mark(loop)
    if early_exit:
        offset=r.slots['q0'];r.emit(f'ld a,(ix{offset:+d})')
        for i in range(1,4 if wide else 2):r.emit(f'or (ix{offset+i:+d})')
        r.emit(f'jp z,{done}')
    r.emit(f"bit 0,(ix{r.slots['q0']:+d})",f"jp z,{skip}")
    for i in range(size):
        a,b=r.slots['v'+str(i)],r.slots['m'+str(i)]
        r.emit(f"ld l,(ix{a:+d})",f"ld h,(ix{a+1:+d})",f"ld e,(ix{b:+d})",f"ld d,(ix{b+1:+d})","add hl,de" if i==0 else "adc hl,de",f"ld (ix{a:+d}),l",f"ld (ix{a+1:+d}),h")
    r.mark(skip)
    for i in reversed(range(4 if wide else 2)):
        offset=r.slots['q0']+i;r.emit(f"{'srl' if i==(3 if wide else 1) else 'rr'} (ix{offset:+d})")
    for i in range(size*2):
        offset=r.slots['m0']+i;r.emit(f"{'sla' if i==0 else 'rl'} (ix{offset:+d})")
    r.emit('dec b',f"jp nz,{loop}");r.mark(done);r.expr('out')
    for i in range(size):
        o=r.slots['v'+str(i)];r.emit(f"ld e,(ix{o:+d})",f"ld d,(ix{o+1:+d})","ld (hl),e","inc hl","ld (hl),d","inc hl")
    r.ret();return r.finish()


def _sat_add():
    r=ShapeRoutine("sat_add",("a","b"));r.expr('a');r.emit('push hl');r.expr('b');r.emit('ex de,hl','pop hl','ld b,h','add hl,de')
    end=r.label();r.emit('ld a,b','xor d','bit 7,a',f'jp nz,{end}','ld a,b','xor h','bit 7,a',f'jp z,{end}','bit 7,d','ld hl,32767',f'jp z,{end}','ld hl,32768');r.mark(end)
    r.emit(f'jp {r.name}_return');return r.finish()


def _extent(ellipse):
    name='ellipse_extent' if ellipse else 'circle_extent'
    r=ShapeRoutine(name,('rx','ry','dy','rxlo','rxhi','rylo','ryhi'),('lo','hi','mid')+words('square',2)+words('remaining',2)+words('rhs',4)+words('lhs',4))
    r.if_(('ult','ry','dy'),lambda:r.ret(-1))
    r.set('lo',0);r.set('hi','rx')
    r.expr(invoke('mul16','dy','dy',slot('square0')))
    r.expr('rylo');r.emit('push hl');r.expr('square0');r.emit('ex de,hl','pop hl','or a','sbc hl,de');r._store('remaining0')
    r.expr('ryhi');r.emit('push hl');r.expr('square1');r.emit('ex de,hl','pop hl','sbc hl,de');r._store('remaining1')
    if ellipse:r.expr(invoke('mul32','rxlo','rxhi','remaining0','remaining1',slot('rhs0')))
    def search():
        r.expr(add(add('lo','hi'),1));r.emit('srl h','rr l');r._store('mid')
        r.expr(invoke('mul16','mid','mid',slot('square0')))
        if ellipse:r.expr(invoke('mul32','square0','square1','rylo','ryhi',slot('lhs0')))
        smaller,larger,equal=r.label(),r.label(),r.label()
        for i in reversed(range(4 if ellipse else 2)):
            r.expr('lhs'+str(i) if ellipse else 'square'+str(i));r.emit('push hl');r.expr('rhs'+str(i) if ellipse else 'remaining'+str(i));r.emit('ex de,hl','pop hl','or a','sbc hl,de',f'jp c,{smaller}',f'jp nz,{larger}')
        r.mark(smaller);r.set('lo','mid');r.emit(f'jp {equal}');r.mark(larger);r.set('hi',sub('mid',1));r.mark(equal)
    r.while_(('ult','lo','hi'),search);r.ret('lo');return r.finish()


def _curve(ellipse):
    name='ellipse' if ellipse else 'circle'
    params=('cx','cy','rx','ry','filled') if ellipse else ('cx','cy','radius','filled')
    r=ShapeRoutine(name,params,(() if ellipse else ('rx','ry'))+('top','bottom','y','previous','current','next','inner','dy','left','right')+words('xsq',2)+words('ysq',2))
    if not ellipse:r.set('rx','radius');r.set('ry','radius')
    for radius in ('rx','ry'):r.if_(('negative',radius),lambda:r.ret())
    r.set('top',invoke('sat_add','cy',sub(0,'ry')));r.set('bottom',invoke('sat_add','cy','ry'));r.clip_interval('top','bottom',64)
    r.expr(invoke('mul16','rx','rx',slot('xsq0')));r.expr(invoke('mul16','ry','ry',slot('ysq0')))
    extent='ellipse_extent' if ellipse else 'circle_extent'
    def extent_at(y,target):
        r.set('dy',sub(y,'cy'));r.if_(('slt',y,'cy'),lambda:r.set('dy',sub('cy',y)))
        r.set(target,invoke(extent,'rx','ry','dy','xsq0','xsq1','ysq0','ysq1'))
    def span(left,right):r.expr(invoke('h_line',left,'y',right,'y'))
    r.set('y','top');extent_at(sub('y',1),'previous');extent_at('y','current')
    def row():
        extent_at(add('y',1),'next')
        r.set('left',invoke('sat_add','cx',sub(0,'current')));r.set('right',invoke('sat_add','cx','current'))
        def outline():
            r.set('inner','current')
            for neighbour in ('previous','next'):r.if_(('slt',neighbour,'inner'),lambda neighbour=neighbour:r.set('inner',neighbour))
            r.if_(('slt','inner','current'),lambda:r.set('inner',add('inner',1)))
            span('left',invoke('sat_add','cx',sub(0,'inner')));span(invoke('sat_add','cx','inner'),'right')
        r.if_(('ne','filled',0),lambda:span('left','right'),outline)
        r.set('previous','current');r.set('current','next');r.set('y',add('y',1))
    r.while_(('slt','y',add('bottom',1)),row);r.ret();return r.finish()


def _intersection():
    r=ShapeRoutine('intersection',('x1','y1','x2','y2','y'),('dx','dy','negative','a','b','remainder','quotient')+words('product',2))
    def swap():
        for a,b in (('x1','x2'),('y1','y2')):
            r.expr(a);r.emit('push hl');r.set(a,b);r.emit('pop hl');r._store(b)
    r.if_(('slt','y2','y1'),swap)
    r.set('negative',0);r.set('dx',sub('x2','x1'))
    r.if_(('slt','x2','x1'),lambda:(r.set('dx',sub('x1','x2')),r.set('negative',1)))
    r.set('dy',sub('y2','y1'));r.expr(invoke('mul16','dx',sub('y','y1'),slot('product0')))
    r.set('quotient','product0');r.set('remainder','product1')
    loop,subtract,restore,done=r.label(),r.label(),r.label(),r.label();r.emit('ld b,16');r.mark(loop)
    for name,opcode in (('quotient','sla'),('remainder','rl')):
        o=r.slots[name];r.emit(f'{opcode} (ix{o:+d})',f'rl (ix{o+1:+d})')
    r.emit(f'jp c,{subtract}');r.expr('remainder');r.emit('push hl');r.expr('dy');r.emit('ex de,hl','pop hl','or a','sbc hl,de',f'jp c,{restore}',f'jp {done}')
    r.mark(subtract);r.expr('remainder');r.emit('push hl');r.expr('dy');r.emit('ex de,hl','pop hl','or a','sbc hl,de',f'jp {done}')
    r.mark(restore);r.emit('add hl,de');r._store('remainder');skip=r.label();r.emit(f'jp {skip}');r.mark(done);r._store('remainder');r.emit(f'set 0,(ix{r.slots["quotient"]:+d})');r.mark(skip);r.emit('dec b',f'jp nz,{loop}')
    r.if_(('ne','negative',0),lambda:r.set('quotient',sub(0,'quotient')),lambda:r.if_(('ne','remainder',0),lambda:r.set('quotient',add('quotient',1))))
    r.ret(add('x1','quotient'));return r.finish()


def _polygon(open_line=False,shifted=False):
    r=ShapeRoutine('offset_polyline' if shifted else 'polyline' if open_line else 'polygon',('points','closed' if open_line else 'filled')+(('ox','oy') if shifted else ()),('n','i','j','x1','y1','x2','y2','top','bottom','y','workspace','buffer','count','value','position','previous','cursor'))
    r.set('n',word(add('points',2)));r.if_(('eq','n',0),lambda:r.ret())
    def point(index,x,y):
        pointer=word(add(word('points'),add(index,index)));r.set('cursor',word(pointer));r.set(x,word('cursor'));r.set(y,word(add('cursor',2)))
        if shifted:r.set(x,invoke('sat_add',x,'ox'));r.set(y,invoke('sat_add',y,'oy'))
    point(0,'x1','y1')
    r.if_(('eq','n',1),lambda:(r.pixel('x1','y1'),r.ret()))
    if not open_line:
        def fill():
            r.set('top','y1');r.set('bottom','y1');r.set('i',1)
            def bounds():
                point('i','x2','y2');r.if_(('slt','y2','top'),lambda:r.set('top','y2'));r.if_(('slt','bottom','y2'),lambda:r.set('bottom','y2'));r.set('i',add('i',1))
            r.while_(('ult','i','n'),bounds)
            # Do not return from the whole polygon when fill misses the screen:
            # outlines still use the ordinary clipped line rasterizer.
            end=r.label();r.false_jump(('sge','bottom',0),end);r.false_jump(('slt','top',64),end)
            r.if_(('slt','top',0),lambda:r.set('top',0));r.if_(('sge','bottom',64),lambda:r.set('bottom',63))
            r.set('workspace',('call','__arr_new',2));r.expr(('call','__arr_reserve','workspace','n'));r.set('buffer',word('workspace'));r.set('y','top')
            def row():
                r.set('count',0);r.set('i',0)
                def edge():
                    r.set('j',add('i',1));r.if_(('eq','j','n'),lambda:r.set('j',0));point('i','x1','y1');point('j','x2','y2')
                    insert,end_edge=r.label(),r.label()
                    r.if_(('slt','y1','y2'),lambda:(r.false_jump(('sge','y','y1'),end_edge),r.false_jump(('slt','y','y2'),end_edge)),lambda:(r.false_jump(('sge','y','y2'),end_edge),r.false_jump(('slt','y','y1'),end_edge)))
                    r.set('value',invoke('intersection','x1','y1','x2','y2','y'));r.set('position','count')
                    def shift():
                        r.set('previous',word(add('buffer',add(sub('position',1),sub('position',1)))))
                        r.false_jump(('slt','value','previous'),insert)
                        r.write(add('buffer',add('position','position')),'previous');r.set('position',sub('position',1))
                    r.while_(('ne','position',0),shift);r.mark(insert);r.write(add('buffer',add('position','position')),'value');r.set('count',add('count',1))
                    r.mark(end_edge);r.set('i',add('i',1))
                r.while_(('ult','i','n'),edge);r.set('i',0)
                def span():
                    r.set('x1',word(add('buffer',add('i','i'))));r.set('x2',word(add('buffer',add(add('i','i'),2))))
                    r.if_(('slt','x1','x2'),lambda:r.expr(invoke('h_line','x1','y',sub('x2',1),'y')));r.set('i',add('i',2))
                r.while_(('ult',add('i',1),'count'),span);r.set('y',add('y',1))
            r.while_(('slt','y',add('bottom',1)),row);r.expr(('call','__arr_release','workspace'));r.mark(end)
        r.if_(('ne','filled',0),fill)
    r.set('i',1)
    # Fill processing may have changed the endpoint scratch slots.
    point(0,'x1','y1')
    def edge():
        point('i','x2','y2');r.expr(invoke('line','x1','y1','x2','y2'));r.set('x1','x2');r.set('y1','y2');r.set('i',add('i',1))
    r.while_(('ult','i','n'),edge)
    def close():
        point(0,'x2','y2');r.expr(invoke('line','x1','y1','x2','y2'))
    if open_line:r.if_(('ne','closed',0),close)
    else:close()
    r.ret();return r.finish()


def assembly(used):
    out=[]
    if 'background' in used:
        r=ShapeRoutine('background',('color',),('byte',));r.set('byte',0);r.if_(('slt','color',128),lambda:r.set('byte',255))
        r.expr('byte');r.emit('ld a,l','ld hl,0x9340','ld (hl),a','ld de,0x9341','ld bc,767','ldir');r.ret();out.append(r.finish())
    if 'sin_raw' in used:
        import math
        r=ShapeRoutine('sin_raw',('angle',),('phase','negative','value'))
        r.expr('angle');r.emit('ld de,1608','call __math_mod');r._store('phase');r.set('negative',0)
        r.if_(('uge','phase',804),lambda:(r.set('phase',sub('phase',804)),r.set('negative',1)))
        r.if_(('uge','phase',403),lambda:r.set('phase',sub(804,'phase')))
        r.set('value',('byte',add('@__gfx_sin_quarter','phase')))
        r.if_(('ne','phase',0),lambda:r.if_(('eq','value',0),lambda:r.set('value',256)))
        r.if_(('ne','negative',0),lambda:r.ret(sub(0,'value')));r.ret('value')
        out.append(r.finish()+'\n__gfx_sin_quarter:\n        db '+','.join(str(round(math.sin(i*math.pi/804)*256)&255) for i in range(403)))
    if used & {'sat_add','stroke'} and not used & {'circle','ellipse'}:out.append(_sat_add())
    if used & {'circle','ellipse'}:
        out.extend([_sat_add(),_multiply(False)])
        if 'circle' in used:out.extend([_extent(False),_curve(False)])
        if 'ellipse' in used:out.extend([_multiply(True),_extent(True),_curve(True)])
    if 'polygon' in used:
        if not used & {'circle','ellipse'}:out.append(_multiply(False))
        out.extend([_intersection(),_polygon()])
    if 'polyline' in used:out.append(_polygon(True))
    if 'stroke' in used:
        out.append(_polygon(True,True))
        r=ShapeRoutine('stroke',('points','closed','width'),('half','low','high','x','y'))
        r.if_(('slt','width',1),lambda:r.ret());r.if_(('sge','width',192),lambda:r.set('width',191))
        r.expr('width');r.emit('srl h','rr l');r._store('half');r.set('low',sub(0,'half'));r.set('high',sub(sub('width','half'),1));r.set('y','low')
        def row():
            r.set('x','low')
            def column():r.expr(invoke('offset_polyline','points','closed','x','y'));r.set('x',add('x',1))
            r.while_(('slt','x',add('high',1)),column);r.set('y',add('y',1))
        r.while_(('slt','y',add('high',1)),row);r.ret();out.append(r.finish())
    return '\n\n'.join(out)
