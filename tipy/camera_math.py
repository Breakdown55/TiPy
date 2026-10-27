"""Small native integer kernels shared by the fixed-point camera and matrices."""
from .shape_runtime import ShapeRoutine,slot,words,_multiply
from .list_runtime import add,sub,word

def invoke(name,*args):return ('call','__gfx_'+name,*args)

class NativeRoutine(ShapeRoutine):
    def load(self,name,pointer,count=2):
        # Multiword locals use contiguous little-endian slots. Copy the block
        # once instead of rebuilding both addresses for every word.
        self.expr(pointer);self.emit('push hl');self.expr(slot(name+'0'))
        self.emit('ex de,hl','pop hl','push bc',f'ld bc,{count*2}','ldir','pop bc')
    def save(self,name,pointer,count=2):
        self.expr(pointer);self.emit('push hl');self.expr(slot(name+'0'))
        self.emit('pop de','push bc',f'ld bc,{count*2}','ldir','pop bc')
    def zero(self,name,count=2):
        self.emit('ld hl,0')
        for i in range(count):self._store(name+str(i))
    def binary(self,target,a,b,subtract=False,count=2,checked=False):
        if checked:
            self.expr(a+str(count-1));self.emit('ld b,h');self.expr(b+str(count-1));self.emit('ld c,h')
        if count==4:
            self.emit('push bc');self.expr(slot(a+'0'));self.emit('push hl')
            self.expr(slot(b+'0'));self.emit('push hl');self.expr(slot(target+'0'))
            self.emit('ld b,h','ld c,l','pop de','pop hl',f'call __gfx_{"sub" if subtract else "add"}64','pop bc')
            if checked:self.expr(target+str(count-1))
        else:
            for i in range(count):
                self.expr(a+str(i));self.emit('push hl');self.expr(b+str(i));self.emit('ex de,hl','pop hl')
                if i==0:self.emit('or a' if subtract else 'nop')
                self.emit(('sbc hl,de' if subtract else 'add hl,de') if i==0 else ('sbc hl,de' if subtract else 'adc hl,de'));self._store(target+str(i))
        if checked:
            end=self.label();self.emit('ld a,b','xor c','bit 7,a',f'jp nz,{end}','ld a,b','xor h','bit 7,a',f'jp z,{end}');self.fail(14);self.mark(end)
    def negate(self,name,count=2):
        if count in {2,4}:
            self.expr(slot(name+'0'));self.emit(f'call __gfx_neg{count*16}');return
        for i in range(count):
            self.expr(name+str(i));self.emit('ld a,l','cpl','ld l,a','ld a,h','cpl','ld h,a');self._store(name+str(i))
        self.emit('or a')
        for i in range(count):
            self.expr(name+str(i));self.emit('ld de,1' if i==0 else 'ld de,0','add hl,de' if i==0 else 'adc hl,de');self._store(name+str(i))
    def extend(self,name,value):
        self.set(name+'0',value);self.set(name+'1',0);self.if_(('negative',value),lambda:self.set(name+'1',65535))
    def compare(self,a,b,less,equal,greater,signed=True,count=2):
        for i in reversed(range(count)):
            self.expr(a+str(i));self.emit('push hl');self.expr(b+str(i));self.emit('ex de,hl','pop hl')
            if signed and i==count-1:self.emit('ld a,h','xor 128','ld h,a','ld a,d','xor 128','ld d,a')
            self.emit('or a','sbc hl,de',f'jp c,{less}',f'jp nz,{greater}')
        self.emit(f'jp {equal}')
    def shift(self,name,right=True,signed=False,count=2):
        for i in (reversed(range(count*2)) if right else range(count*2)):
            offset=self.slots[name+'0']+i
            op=('sra' if signed else 'srl') if right and i==count*2-1 else 'rr' if right else 'sla' if i==0 else 'rl'
            self.emit(f'{op} (ix{offset:+d})')


def routines():
    out=[_multiply(True).replace('__gfx_mul32','__gfx_u_mul32')]
    # Register ABI: HL=left, DE=right, BC=result. Pair increments preserve
    # carry, so the same kernels work in-place without temporary buffers.
    for subtract in (False,True):
        code=['__gfx_'+('sub' if subtract else 'add')+'64:']
        if subtract:code.extend(['        ex de,hl','        or a'])
        for i in range(8):
            code.extend(['        ld a,(de)'])
            if subtract:code.append('        sbc a,(hl)')
            else:code.append('        '+('add' if i==0 else 'adc')+' a,(hl)')
            code.extend(['        ld (bc),a','        inc hl','        inc de','        inc bc'])
        out.append('\n'.join(code+['        ret']))
    out.append('''__gfx_neg32:
        push bc
        ld b,4
        jr __gfx_neg_bytes
__gfx_neg64:
        push bc
        ld b,8
__gfx_neg_bytes:
        xor a
__gfx_neg_loop:
        ld a,0
        sbc a,(hl)
        ld (hl),a
        inc hl
        djnz __gfx_neg_loop
        pop bc
        ret''')
    r=NativeRoutine('s_mul32',('a','b','out'),words('x',2)+words('y',2)+words('p',4)+('negative',))
    r.load('x','a');r.load('y','b');r.set('negative',0)
    for name in ('x','y'):
        r.if_(('negative',name+'1'),lambda name=name:(r.negate(name),r.set('negative',sub(1,'negative'))))
    r.expr(invoke('u_mul32','x0','x1','y0','y1',slot('p0')))
    r.if_(('ne','negative',0),lambda:r.negate('p',4));r.save('p','out',4);r.ret();out.append(r.finish())

    r=NativeRoutine('u_div64',('n','d','q','remainder'),words('quot',2)+words('rem',2)+words('den',2))
    r.load('quot','n');r.load('rem',add('n',4));r.load('den','d')
    ok,bad=r.label(),r.label();r.compare('rem','den',ok,bad,bad,False);r.mark(bad);r.fail(14);r.mark(ok)
    loop,subtract,store,keep,again=r.label(),r.label(),r.label(),r.label(),r.label();r.emit('ld b,32');r.mark(loop)
    r.shift('quot',False)
    for i in range(4):r.emit(f'rl (ix{r.slots["rem0"]+i:+d})')
    r.emit(f'jp c,{subtract}')
    r.compare('rem','den',keep,subtract,subtract,False)
    r.mark(subtract);r.binary('rem','rem','den',True);r.emit(f'set 0,(ix{r.slots["quot0"]:+d})',f'jp {again}')
    r.mark(keep);r.mark(again);r.emit('dec b',f'jp nz,{loop}')
    r.save('quot','q');r.if_(('ne','remainder',0),lambda:r.save('rem','remainder'));r.ret();out.append(r.finish())

    r=NativeRoutine('muldiv32',('a','b','d','out'),words('x',2)+words('y',2)+words('p',4))
    r.load('x','a');r.load('y','b');r.expr(invoke('u_mul32','x0','x1','y0','y1',slot('p0')));r.expr(invoke('u_div64',slot('p0'),'d','out',0));r.ret();out.append(r.finish())

    r=NativeRoutine('lerp32',('a','b','n','d','out'),words('x',2)+words('y',2)+words('delta',2)+words('q',2)+('negative',))
    r.load('x','a');r.load('y','b');r.set('negative',0)
    lower,forward,ready=r.label(),r.label(),r.label();r.compare('x','y',forward,forward,lower)
    r.mark(lower);r.set('negative',1);r.binary('delta','x','y',True);r.emit(f'jp {ready}')
    r.mark(forward);r.binary('delta','y','x',True);r.mark(ready)
    r.expr(invoke('muldiv32',slot('delta0'),'n','d',slot('q0')))
    r.if_(('ne','negative',0),lambda:r.binary('x','x','q',True),lambda:r.binary('x','x','q'))
    r.save('x','out');r.ret();out.append(r.finish())

    r=NativeRoutine('round_coord',('value',),words('p',4)+words('half',4)+('negative',))
    r.load('p','value',4);r.set('negative',0);r.if_(('negative','p3'),lambda:(r.negate('p',4),r.set('negative',1)))
    r.zero('half',4);r.set('half0',32768);r.binary('p','p','half',count=4)
    def saturate():r.if_(('ne','negative',0),lambda:r.ret(-32768));r.ret(32767)
    for i in (2,3):r.if_(('ne','p'+str(i),0),saturate)
    r.if_(('uge','p1',32768),saturate);r.if_(('ne','negative',0),lambda:r.ret(sub(0,'p1')));r.ret('p1');out.append(r.finish())

    r=NativeRoutine('fixed_store',('value','out'),words('p',4)+words('half',4)+('negative',))
    r.load('p','value',4);r.set('negative',0);r.if_(('negative','p3'),lambda:(r.negate('p',4),r.set('negative',1)))
    r.zero('half',4);r.set('half0',32768);r.binary('p','p','half',count=4)
    overflow,good=r.label(),r.label()
    r.if_(('ne','p3',0),lambda:r.emit(f'jp {overflow}'));r.if_(('uge','p2',32768),lambda:r.emit(f'jp {overflow}'));r.emit(f'jp {good}')
    r.mark(overflow);r.if_(('ne','negative',0),lambda:(r.set('p1',0),r.set('p2',32768)),lambda:(r.set('p1',65535),r.set('p2',32767)));r.mark(good)
    r.set('p0','p1');r.set('p1','p2');r.if_(('ne','negative',0),lambda:r.negate('p'));r.save('p','out');r.ret();out.append(r.finish())
    return out


def link(groups,roots):
    """Only include reachable native kernels and their static state."""
    import re
    owners={label:index for index,source in enumerate(groups) for label in re.findall(r'^(__gfx_\w+):',source,re.M)}
    pending=list(roots);live=set()
    while pending:
        name=pending.pop();index=owners.get(name)
        if index is None or index in live:continue
        live.add(index);pending.extend(re.findall(r'\b__gfx_\w+\b',groups[index]))
    return '\n\n'.join(source for i,source in enumerate(groups) if i in live)
