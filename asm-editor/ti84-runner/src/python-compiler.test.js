import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'
import { readFileSync } from 'node:fs'
import { Ti84Runner } from './hardware.js'

function execute(source, measurements) {
    const python = process.env.TIPY_PYTHON || 'python'
    const args = measurements?.optimizeGraphics === false
        ? ['-c','import json,sys; from tipy import compile_source; print(json.dumps(compile_source(sys.stdin.read(), optimize_graphics=False, optimization="reference")))']
        : ['-m','tipy','-','--optimization', measurements?.optimization ?? 'reference','--show','all']
    const processResult = spawnSync(python,args,{
        cwd: resolve('../..'), input: source, encoding: 'utf8'
    })
    assert.ifError(processResult.error)
    assert.equal(processResult.status,0,processResult.stderr)
    const result = JSON.parse(processResult.stdout)
    const runner = new Ti84Runner(result.assembly)
    assert.ok(result.estimated_bytes>=runner.machineBytes,'RAM sizing must never underestimate assembled image bytes')
    if (measurements?.seedGraph) runner.machine.memory.set(measurements.seedGraph,0x9340)
    const frames = []
    if (measurements) {
        const service = runner.serviceBcall.bind(runner)
        let keyIndex=0
        runner.lcd.dirty = false
        runner.serviceBcall = () => {
            const r=runner.machine.z80.regs,mem=runner.machine.memory
            const ret=mem[r.sp] | (mem[(r.sp+1)&65535]<<8)
            const call=mem[ret] | (mem[(ret+1)&65535]<<8)
            if(call===0x4018 && measurements.pollKeys) {
                runner.releaseKeys()
                const key=measurements.pollKeys[keyIndex++] ?? 0
                if(key) runner.setKey(key,true)
            }
            service()
            if (runner.lcd.dirty) { frames.push(runner.lcd.pixels.slice()); runner.lcd.dirty = false }
        }
    }
    for (let i = 0; i < (measurements?.maxSlices ?? 400) && !runner.done; i++) runner.runSlice()
    assert.equal(runner.done,true,'Generated program must return')
    assert.equal(runner.machine.z80.regs.sp,0,'RET should pop the sentinel after a balanced expression stack')
    assert.equal(runner.machine.z80.regs.ix,0,'Functions must restore the caller frame pointer')
    assert.equal(runner.machine.z80.regs.iy,0x89f0,'Functions must preserve the calculator flags pointer')
    if (measurements) Object.assign(measurements, { bytes: runner.machineBytes, tStates: runner.machine.tStateCount,
        screen: runner.lcd.pixels.slice(), graph: runner.machine.memory.slice(0x9340,0x9640),
        report: result.optimization_report, assembly: result.assembly, frames })
    const values = {},mem=runner.machine.memory,memo=new Map()
    const word=a=>mem[a] | (mem[a+1]<<8)
    const fields=text=>{
        const result=[];let depth=0,start=0
        for(let i=0;i<text.length;i++) {
            if(text[i]==='[')depth++
            else if(text[i]===']')depth--
            else if(text[i]===',' && !depth){result.push(text.slice(start,i));start=i+1}
        }
        if(text.slice(start))result.push(text.slice(start))
        return result
    }
    const decode=(address,type)=>{
        if(type==='u8' || type==='bool')return mem[address]
        const raw=word(address)
        if(type==='i32')return (word(word(raw)) | (word(word(raw)+2)<<16))
        if(type==='float')return new DataView(mem.buffer,mem.byteOffset+word(raw),4).getFloat32(0,true)
        if(type==='none')return null
        if(type.startsWith('optional[') && !type.endsWith('[]'))return raw?decode(word(raw),type.slice(9,-1)):null
        if(type==='str')return String.fromCharCode(...mem.slice(word(raw),word(raw)+word(raw+2)))
        const tuple=type==='tuple[]' || /^(tuple|tuple_var|tuple_cycle)\[/.test(type) && !type.endsWith('[]')
        const dict=type.startsWith('dict[') && !type.endsWith('[]')
        const set=type.startsWith('set[') && !type.endsWith('[]')
        if(tuple || dict || set || type.endsWith('[]')) {
            if(memo.has(raw))return memo.get(raw)
            const length=word(raw+2),data=word(raw),value=dict?new Map():set?new Set():[]
            memo.set(raw,value)
            if(dict || set) {
                const [keyType,valueType]=set?[type.slice(4,-1),'i16']:fields(type.slice(5,-1));let entry=word(raw+14),count=0
                while(entry) {
                    assert.ok(count++<length,'Dictionary order links must be finite')
                    if(set)value.add(decode(entry+4,keyType));else value.set(decode(entry+4,keyType),decode(entry+6,valueType));entry=word(entry+8)
                }
                assert.equal(count,length)
            } else {
                const types=tuple?fields(type.slice(type.indexOf('[')+1,-1)):null,step=word(raw+6)===1?1:2
                for(let i=0;i<length;i++)value.push(decode(data+i*step,types?types[i%types.length]:type.slice(0,-2)))
            }
            return value
        }
        return raw&0x8000?raw-65536:raw
    }
    for (const [name,symbol] of Object.entries(result.symbols)) values[name]=decode(runner.assembly.asm.scopes[0].symbols.get(symbol.label).value,symbol.type)
    return values
}

test('Native binary32 agrees with rounded arithmetic across varied exponents and IEEE edge cases', () => {
    const cases=[[1.0000001192092896,0.9999999403953552],[16777216,1],[-16777216,1],[1e-20,-1e-20],[3.4e38,1.0001],[1e-38,1e-7],[-1e20,3.14],[0.125,-0.5],[123456.75,0.00003125],[1e-30,1e30],[1.17549435e-38,0.99999994],[1.40129846e-45,2]]
    const source=cases.map(([a,b],i)=>`float a${i}=${Math.fround(a).toExponential(16)}\nfloat b${i}=${Math.fround(b).toExponential(16)}\ns${i}=a${i}+b${i}\nd${i}=a${i}-b${i}\nm${i}=a${i}*b${i}\nq${i}=a${i}/b${i}\n`).join('')
    const v=execute(source,{maxSlices:16000})
    for(let i=0;i<cases.length;i++) {
        const [a,b]=cases[i].map(Math.fround)
        for(const [prefix,expected] of [['s',Math.fround(a+b)],['d',Math.fround(a-b)],['m',Math.fround(a*b)],['q',Math.fround(a/b)]])assert.equal(v[prefix+i],expected,`${prefix}: ${a}, ${b}`)
    }
    const edge=execute(`nan=float("nan")
inf=float("inf")
bool unordered=nan!=nan
bool ordered=nan==nan
bool greater=inf>float(0)
bool less=float(-0.0)<float(0.0)
tie=float("1.000000059604644775390625")
above=float("1.000000059604644775390626")
text=str(inf)
nan_text=str(nan)
power=float(2)**-3
`,{maxSlices:16000})
    assert.deepEqual([edge.unordered,edge.ordered,edge.greater,edge.less],[1,0,1,0]);assert.equal(edge.tie,1);assert.equal(edge.above,Math.fround(1+2**-23));assert.equal(edge.text,'inf');assert.equal(edge.nan_text,'nan')
    assert.equal(edge.power,0.125)
    const integers=execute('rounded=round(12345,-2)\nwide_rounded=round(i32(1234567890),-5)',{maxSlices:4000})
    assert.equal(integers.rounded,12300);assert.equal(integers.wide_rounded,1234600000)
})

test('Native decimal parsing and float rounding are independent of the host', () => {
    const v=execute(`a=float("1.1")
b=float(" -2.675e+0 ")
c=float("1e-45")
d=float("3.4028235e38")
e=round(float(2.5))
f=round(float(3.5))
g=round(float(-2.5))
h=round(float(2.675),2)
i=round(float(12345.0),-2)
text=str(h)
`,{maxSlices:20000})
    assert.deepEqual([v.a,v.b,v.c,v.d],[Math.fround(1.1),Math.fround(-2.675),Math.fround(1e-45),Math.fround(3.4028235e38)])
    assert.deepEqual([v.e,v.f,v.g,v.h,v.i],[2,4,-2,Math.fround(2.67),12300]);assert.equal(v.text,'2.67')
})

test('Native binary32 text uses exact decimal digits and fixed precision rounding', () => {
    const v=execute(`a=str(float(1.1))
b=str(float(-0.0))
c=str(float(1e20))
d=str(float(1e-8))
e=f"{float(2.675):.2f}"
f=f"{float(12.5):08.2f}"
g=f"{float(9.999):.2f}"
h=f"{float(-0.001):.2f}"
`,{maxSlices:16000})
    assert.deepEqual([v.a,v.b,v.c,v.d,v.e,v.f,v.g,v.h],['1.1','-0.0','1e+20','1e-08','2.67','00012.50','10.00','-0.00'])
})

test('Native binary32 arithmetic handles signs, rounding, large values and subnormals', () => {
    const v=execute(`float a=1.1
float b=-3.25
sum=a+b
diff=a-b
product=a*b
quotient=a/b
large=float(1e20)*float(1e10)
small=float(1e-30)*float(1e-10)
cancel=float(1.0)-float(1.0)
converted=float(i32(2147483647))
from_fixed=float(1.5)
integer=int(float(-12.75))
wide=i32(float(100000.75))
bool less=a<b
bool greater=a>b
bool equal=float(0.0)==float(-0.0)
`,{maxSlices:12000})
    const a=Math.fround(1.1),b=Math.fround(-3.25)
    assert.deepEqual([v.sum,v.diff,v.product,v.quotient],[Math.fround(a+b),Math.fround(a-b),Math.fround(a*b),Math.fround(a/b)])
    assert.equal(v.large,Math.fround(Math.fround(1e20)*Math.fround(1e10)));assert.equal(v.small,Math.fround(Math.fround(1e-30)*Math.fround(1e-10)))
    assert.equal(v.cancel,0);assert.equal(v.converted,Math.fround(2147483647));assert.equal(v.from_fixed,1.5);assert.equal(v.integer,-12);assert.equal(v.wide,100000)
    assert.deepEqual([v.less,v.greater,v.equal],[0,1,1])
})

test('Native i32 arithmetic keeps signed 32-bit values behind the word-sized calling convention', () => {
    const v=execute(`i32 a=100000
i32 b=-70000
sum=a+b
diff=a-b
product=a*b
quotient=a//b
remainder=a%b
left=a<<10
right=b>>5
inverse=~a
negative=-a
power=i32(3)**15
bool less=b<a
bool equal=a==i32(100000)
text=str(a)
min_text=str(i32(-2147483648))
max_text=str(i32(2147483647))
hex_text=hex(i32(-2147483648))
formatted=f"{a:012d}"
ordered=sorted([i32(65535),i32(-1),i32(65536)])
def increment(i32 value) -> i32:
    return value+1
returned=increment(a)
low=int(a)
`,{maxSlices:12000})
    assert.deepEqual([v.a,v.b,v.sum,v.diff,v.product,v.quotient,v.remainder],[100000,-70000,30000,170000,Math.imul(100000,-70000),-2,-40000])
    assert.deepEqual([v.left,v.right,v.inverse,v.negative,v.power,v.less,v.equal,v.returned,v.low],[102400000,-2188,~100000,-100000,14348907,1,1,100001,-31072])
    assert.equal(v.text,'100000');assert.equal(v.min_text,'-2147483648');assert.equal(v.max_text,'2147483647')
    assert.equal(v.hex_text,'-0x80000000');assert.equal(v.formatted,'000000100000');assert.deepEqual(v.ordered,[-1,65535,65536])
})

test('Native scalar division, modular power, divmod and ties-to-even rounding', () => {
    const v=execute(`calls=[]
def number(int x):
    calls.append(x)
    return x
pair=divmod(number(-17),number(5))
q,r=pair
positive=pow(32760,17,32749)
negative=pow(32760,17,-32749)
power=pow(3,5)
a=round(2.5)
b=round(3.5)
c=round(-2.5)
d=round(-3.5)
fraction=7/2
negative_fraction=-7/2
mixed=200/100.0
reverse=100.0/200
text=str(fraction)
negative_text=str(negative_fraction)
`,{maxSlices:7000})
    assert.deepEqual(v.calls,[-17,5]);assert.deepEqual([v.q,v.r,v.power,v.a,v.b,v.c,v.d],[-4,3,243,2,4,-2,-4])
    const modulus=32749n,power=32760n**17n
    assert.equal(v.positive,Number(power%modulus));assert.equal(v.negative,Number(power%modulus-modulus));assert.equal(v.text,'3.5');assert.equal(v.negative_text,'-3.5')
    assert.equal(v.mixed,2);assert.equal(v.reverse,0.5)
})

test('Native classes own typed attributes and methods preserve instance state and aliases', () => {
    const v=execute(`class Counter:
    def __init__(self,int start,str name="counter"):
        self.value=start
        self.name=name
        self.history=[start]
    def add(self,int amount=1):
        self.value+=amount
        self.history.append(self.value)
        return self.value
    def rename(self,str text):
        self.name=text
    def label(self):
        return self.name+":"+str(self.value)
first=Counter(10,"A")
second=Counter(100)
alias=first
a=first.add(2)
b=alias.add()
c=second.add(7)
callback=first.add
d=callback(3)
first.rename("B")
label=first.label()
history=first.history.copy()
bool shared=first is alias
bool distinct=first!=second
temporary=Counter(0)
for repetition in range(60):
    temporary=Counter(repetition)
    temporary.rename("name"+str(repetition))
last=temporary.label()
`,{maxSlices:10000})
    assert.deepEqual([v.a,v.b,v.c,v.d],[12,13,107,16]);assert.equal(v.label,'B:16');assert.deepEqual(v.history,[10,12,13,16]);assert.equal(v.shared,1);assert.equal(v.distinct,1);assert.equal(v.last,'name59:59')
})

test('Generator stacks and nested definition-time defaults remain independent across repeated calls', () => {
    const v=execute(`def factory(int initial):
    def append(int value,int[] data=[initial]):
        data.append(value)
        return data.copy()
    return append
first=factory(10)
second=factory(20)
a=first(1)
b=first(2)
c=second(3)
def inner(int count):
    for n in range(count):
        yield str(n)
def outer(int count):
    for text in inner(count):
        yield text+"!"
last=[]
for repetition in range(30):
    last=list(outer(3))
`,{maxSlices:16000})
    assert.deepEqual(v.a,[10,1]);assert.deepEqual(v.b,[10,1,2]);assert.deepEqual(v.c,[20,3]);assert.deepEqual(v.last,['0!','1!','2!'])
})

test('Native generators suspend loops and handlers, remain lazy and propagate errors', () => {
    const v=execute(`events=[]
def numbers(int count):
    for n in range(count):
        events.append(n)
        yield n*n
first=numbers(4)
second=numbers(2)
before=len(events)
a=next(first)
b=next(first)
c=next(second)
rest=list(first)
end=next(first,99)
def guarded():
    try:
        yield 10
        raise ValueError("after yield")
    finally:
        events.append(50)
generator=guarded()
d=next(generator)
caught=False
try:
    next(generator)
except ValueError:
    caught=True
total=0
for n in numbers(3):
    total+=n
`,{maxSlices:12000})
    assert.equal(v.before,0);assert.deepEqual([v.a,v.b,v.c,v.d,v.end,v.total],[0,1,0,10,99,5]);assert.deepEqual(v.rest,[4,9]);assert.equal(v.caught,1)
    assert.deepEqual(v.events,[0,1,0,2,3,50,0,1,2])
})

test('None uses optional storage and identity preserves object aliases', () => {
    const v=execute(`x=None
bool missing=x is None
x=7
value=0
if x is not None:
    value=x+1
data=[1]
alias=data
other=[1]
bool same=data is alias
bool different=data is not other
def maybe(bool choose):
    if choose:
        return "yes"
    return None
answer=maybe(True)
text="no"
if answer is not None:
    text=answer+"!"
empty=maybe(False)
bool absent=empty is None
`,{maxSlices:4000})
    assert.deepEqual([v.missing,v.value,v.same,v.different,v.absent],[1,8,1,1,1]);assert.equal(v.text,'yes!')
})

test('Native iterators, reversed, lazy map/filter and next preserve state and ownership', () => {
    const v=execute(`calls=[]
def twice(int n):
    calls.append(n)
    return n*2
def even(int n):
    return n%2==0
data=[1,2,3]
iterator=iter(data)
first=next(iterator)
data.append(4)
rest=list(iterator)
end=next(iterator,99)
back=list(reversed(data))
mapped=map(twice,data)
before=len(calls)
head=next(mapped)
after=len(calls)
tail=list(mapped)
filtered=list(filter(even,data))
names=list(reversed("abc"))
keys=iter({"x":1,"y":2})
key1=next(keys)
key2=next(keys)
stopped=False
try:
    next(keys)
except StopIteration:
    stopped=True
total=0
for n in map(lambda x:x+1,data):
    total+=n
`,{maxSlices:10000})
    assert.equal(v.first,1);assert.deepEqual(v.rest,[2,3,4]);assert.equal(v.end,99);assert.deepEqual(v.back,[4,3,2,1])
    assert.equal(v.before,0);assert.equal(v.head,2);assert.equal(v.after,1);assert.deepEqual(v.tail,[4,6,8]);assert.deepEqual(v.filtered,[2,4]);assert.deepEqual(v.names,['c','b','a'])
    assert.equal(v.key1,'x');assert.equal(v.key2,'y');assert.equal(v.stopped,1);assert.equal(v.total,14)
})

test('Exception recovery reclaims abandoned native temporaries and preserves live aliases', () => {
    const v=execute(`live=["keep","alive"]
alias=live
def explode(int n):
    abandoned=[list(range(40)),list(range(40))]
    raise ValueError("temporary "+str(n))
count=0
for iteration in range(80):
    try:
        explode(iteration)
    except ValueError:
        count+=1
last=alias[1]
`,{maxSlices:12000})
    assert.equal(v.count,80);assert.equal(v.last,'alive');assert.deepEqual(v.live,['keep','alive'])
})

test('Native exception handlers unwind calls and preserve finally on return break and rethrow', () => {
    const v=execute(`events=[]
def fail(int n):
    temporary=list(range(20))
    if n==0:
        raise ValueError("bad value")
    return 100//n
def recover(int n):
    try:
        return fail(n)
    except ValueError as err:
        events.append(1)
        return 7
    finally:
        events.append(2)
a=recover(0)
b=recover(5)
message=""
try:
    fail(0)
except ValueError as error:
    message=str(error)
zero=0
try:
    zero=10//0
except ZeroDivisionError:
    zero=99
outer=0
try:
    try:
        raise KeyError("missing")
    except ValueError:
        outer=1
    finally:
        events.append(3)
except KeyError:
    outer=2
for num in range(3):
    try:
        break
    finally:
        events.append(4)
`,{maxSlices:4000})
    assert.deepEqual([v.a,v.b,v.zero,v.outer],[7,20,99,2]);assert.equal(v.message,'bad value');assert.deepEqual(v.events,[1,2,2,3,4])
})

test('Native closures share captured cells after return and lambda calls remain independent', () => {
    for(const optimization of ['reference','balanced','aggressive']) {
        const v=execute(`def make(int start):
    state=start
    def increment(int amount):
        nonlocal state
        state+=amount
        return state
    return increment
first=make(10)
second=make(100)
a=first(2)
b=first(3)
c=second(7)
def multiplier(int factor):
    return lambda x: x*factor
double=multiplier(2)
d=double(6)
def text_holder(str start):
    text=start
    def change(int n):
        nonlocal text
        text=text+str(n)
        return text
    return change
holder=text_holder("A")
one=holder(1)
two=holder(2)
for iteration in range(150):
    first=make(iteration)
e=first(2)
`,{maxSlices:3000,optimization})
        assert.deepEqual([v.a,v.b,v.c,v.d,v.e],[12,15,107,12,151]);assert.equal(v.one,'A1');assert.equal(v.two,'A12')
    }
})

test('Native comprehensions, bitwise precedence and set/dictionary operators', () => {
    const v=execute(`d={str(x):[x*x] for x in range(5) if x%2==0}
s={x%3 for x in range(8)}
merged=d | {"0":[99],"a":[6]}
u=s | {4}
i=s & {1,4}
diff=s-{1}
xor=s ^ {1,4}
bits=3 | 4 & 6 ^ 1
inverse=~5
left=3<<4
right=-65>>3
large=-10>>20
zero=7<<20
exponent=2**3**2
negative=-2**2
`,{maxSlices:3000})
    assert.deepEqual(v.d,new Map([['0',[0]],['2',[4]],['4',[16]]]))
    assert.deepEqual(v.merged,new Map([['0',[99]],['2',[4]],['4',[16]],['a',[6]]]))
    assert.deepEqual(v.s,new Set([0,1,2]));assert.deepEqual(v.u,new Set([0,1,2,4]));assert.deepEqual(v.i,new Set([1]));assert.deepEqual(v.diff,new Set([0,2]));assert.deepEqual(v.xor,new Set([0,2,4]))
    assert.deepEqual([v.bits,v.inverse,v.left,v.right,v.large,v.zero,v.exponent,v.negative],[7,-6,48,-9,-1,0,512,-4])
})

test('Native defaults are shared definition-time values and keyword calls preserve evaluation order', () => {
    const v=execute(`events=[]
def value(int x):
    events.append(x)
    return x
def add(int a,int b=5):
    return a+b
def collect(int x,int[] values=[]):
    values.append(x)
    return values.copy()
def total(int initial,*args):
    for num in args:
        initial+=num
    return initial
a=add(b=value(2),a=value(1))
b=add(10)
c=collect(1)
d=collect(2)
e=total(10,1,2,3)
f=100 if a>2 else value(99)
`,{maxSlices:3000})
    assert.deepEqual(v.events,[2,1]);assert.deepEqual([v.a,v.b,v.e,v.f],[3,15,16,100]);assert.deepEqual(v.c,[1]);assert.deepEqual(v.d,[1,2])
})

test('Loop else only runs after completion and nested breaks belong to their own loop', () => {
    const v=execute(`a=0
for x in range(3):
    if x==1:
        break
else:
    a=99
b=0
for x in range(3):
    for y in range(3):
        break
else:
    b=7
c=0
while c<2:
    c+=1
    continue
else:
    c+=10
`,{maxSlices:3000})
    assert.deepEqual([v.a,v.b,v.c],[0,7,12])
})

test('Native sets preserve hashed membership, algebra, mutation and string ownership', () => {
    const v=execute(`a={1,2,2,3}
b=set([3,4])
union=a.union(b)
common=a.intersection(b)
diff=a.difference(b)
xor=a.symmetric_difference(b)
bool subset=common < a
bool disjoint=diff.isdisjoint(b)
a.add(9)
a.discard(2)
a.remove(1)
copy=a.copy()
a.clear()
names=set(["one","two","one"])
name=names.pop()
names.clear()
bool found=3 in copy
`,{maxSlices:3000})
    assert.deepEqual(v.union,new Set([1,2,3,4]));assert.deepEqual(v.common,new Set([3]))
    assert.deepEqual(v.diff,new Set([1,2]));assert.deepEqual(v.xor,new Set([1,2,4]))
    assert.deepEqual(v.copy,new Set([3,9]));assert.equal(v.subset,1);assert.equal(v.disjoint,1);assert.equal(v.found,1);assert.equal(v.name,'one')
})

test('Stored dictionary views remain live and materialize typed keys values and items', () => {
    const v=execute(`d={"a":[1],"b":[2]}
keys=d.keys()
vals=d.values()
items=d.items()
d["c"]=[3]
count=len(keys)
bool has="c" in keys
bool val=[2] in vals
bool item=("a",[1]) in items
key_list=list(keys)
value_list=list(vals)
item_list=list(items)
total=0
for key,value in items:
    total+=value[0]
d.clear()
after=len(keys)
`,{maxSlices:3000})
    assert.equal(v.count,3);assert.equal(v.after,0);assert.equal(v.has,1);assert.equal(v.val,1);assert.equal(v.item,1);assert.equal(v.total,6)
    assert.deepEqual(v.key_list,['a','b','c']);assert.deepEqual(v.value_list,[[1],[2],[3]]);assert.deepEqual(v.item_list,[['a',[1]],['b',[2]],['c',[3]]])
})

test('Native string indexing and sequence slicing match Python bounds and step rules', () => {
    const values=execute(`s="abcdef"
a=s[1]
b=s[-1]
c=s[1:5:2]
d=s[::-1]
e=s[-100:100]
f=s[4:1:-2]
g=s[0:0]
h=s[::32767]
i=s[::-32768]
names=["a","b","c"]
back=names[::-1]
names.clear()
tuple_value=(1,"x",True)
tail=tuple_value[1:]
`,{maxSlices:3000})
    assert.deepEqual([values.a,values.b,values.c,values.d,values.e,values.f,values.g,values.h,values.i],['b','f','bd','fedcba','abcdef','ec','','a','f'])
    assert.deepEqual(values.back,['c','b','a']);assert.deepEqual(values.tail,['x',1])
})

test('Native strings search, strip, split, join, replace and case conversion preserve immutable values', () => {
    const values=execute(`source="  Alpha beta Alpha  "
trim=source.strip()
left=source.lstrip()
right=source.rstrip()
words=trim.split()
joined="-".join(words)
first=source.find("Alpha")
last=source.rfind("Alpha")
count=source.count("Alpha")
bool starts=trim.startswith("Alpha")
bool ends=trim.endswith("Alpha")
str changed=trim.replace("Alpha","X",1)
`,{maxSlices:3000})
    assert.equal(values.trim,'Alpha beta Alpha');assert.equal(values.left,'Alpha beta Alpha  ');assert.equal(values.right,'  Alpha beta Alpha')
    assert.deepEqual(values.words,['Alpha','beta','Alpha']);assert.equal(values.joined,'Alpha-beta-Alpha')
    assert.equal(values.first,2);assert.equal(values.last,13);assert.equal(values.count,2)
    assert.equal(values.starts,1);assert.equal(values.ends,1);assert.equal(values.changed,'X beta Alpha')
    const cases=execute(`a="hELLo".lower()
b="hello".upper()
c="hELLo WORLD".capitalize()
d="hello WORLD".title()
e="AbC!".swapcase()
f="x,,y,".split(",")
g=" abc def ".split(maxsplit=0)
h="ab".replace("","-",2)
i="abc".find("",4)
j="abc".count("")
`,{maxSlices:3000})
    assert.deepEqual([cases.a,cases.b,cases.c,cases.d,cases.e],['hello','HELLO','Hello world','Hello World','aBc!'])
    assert.deepEqual(cases.f,['x','','y','']);assert.deepEqual(cases.g,['abc def ']);assert.equal(cases.h,'-a-b');assert.equal(cases.i,-1);assert.equal(cases.j,4)
})

test('Keyword binding evaluates method receiver and arguments in their original order', () => {
    const values=execute(`calls=[]
def source():
    calls.append(10)
    return "a:b:c"
def separator():
    calls.append(20)
    return ":"
def maximum():
    calls.append(30)
    return 1
result=source().split(maxsplit=maximum(),sep=separator())
`,{maxSlices:3000})
    assert.deepEqual(values.calls,[10,30,20]);assert.deepEqual(values.result,['a','b:c'])
})

test('Concatenation, repetition and stable sorting preserve list aliases', () => {
    const values=execute(`a=[1]
b=[1]
rows=[b,a]
ordered=sorted(rows)
ordered[0][0]=9
matrix=[[1]]*3
matrix[0][0]=7
joined=[1,2]+[3,4]
numbers=sorted([3,-1,2],reverse=True)
names=["z","a","b"]
names.sort()
text=3*"ab"+"!"
empty="abc"*-2
record=(1,"a")+(2,"b")
twice=(1,"x")*2
`,{maxSlices:3000})
    assert.deepEqual(values.a,[1]);assert.deepEqual(values.b,[9]);assert.strictEqual(values.ordered[0],values.b)
    assert.deepEqual(values.matrix,[[7],[7],[7]]);assert.strictEqual(values.matrix[0],values.matrix[2])
    assert.deepEqual(values.joined,[1,2,3,4]);assert.deepEqual(values.numbers,[3,2,-1]);assert.deepEqual(values.names,['a','b','z'])
    assert.equal(values.text,'ababab!');assert.equal(values.empty,'');assert.deepEqual(values.record,[1,'a',2,'b']);assert.deepEqual(values.twice,[1,'x',1,'x'])
})

test('Native f-strings, scalar conversion and integer formats need no host evaluation', () => {
    const values=execute(`n=7
text=f"score={n:04d}, {'x'!r}"
sign=f"{-3:+05d}"
base=f"{255:#06x}"
left=f"{'x':.<5}"
center=f"{'x':^5}"
binary=bin(-5)
octal=oct(15)
hexadecimal=hex(255)
code=ord("A")
character=chr(66)
boolean=str(True)
integer=str(-32768)
`)
    assert.equal(values.text,"score=0007, 'x'");assert.equal(values.sign,'-0003')
    assert.equal(values.base,'0x00ff')
    assert.equal(values.left,'x....');assert.equal(values.center,'  x  ')
    assert.equal(values.binary,'-0b101');assert.equal(values.octal,'0o17');assert.equal(values.hexadecimal,'0xff')
    assert.equal(values.code,65);assert.equal(values.character,'B');assert.equal(values.boolean,'True');assert.equal(values.integer,'-32768')
})

test('Nested containers and string lists preserve aliases and reclaim owned children', () => {
    const source=`str names=["Alice","Bob"]
str first=names[0]
names.append("Carol")
str removed=names.pop(1)
names.insert(0,"Zoe")
names.reverse()
str copied=names.copy()
names.extend(copied)
int count=names.count("Alice")
names.remove("Zoe")
int matrix=[[1,2],[3,4]]
row=matrix[0]
row[1]=9
matrix.append([5,6])
int total=0
for values in matrix:
    total+=values[1]
dict[str,int[]] data={"row":[7,8]}
held=data["row"]
data.clear()
held.append(9)
bool same=matrix==[[1,9],[3,4],[5,6]]
bool ordered="Alice"<"Bob"
`
    const values=execute(source,{maxSlices:2000})
    assert.deepEqual(values.names,['Carol','Alice','Carol','Alice','Zoe'])
    assert.deepEqual(values.copied,['Carol','Alice','Zoe'])
    assert.equal(values.first,'Alice');assert.equal(values.removed,'Bob');assert.equal(values.count,2)
    assert.deepEqual(values.matrix,[[1,9],[3,4],[5,6]])
    assert.equal(values.total,19);assert.deepEqual(values.held,[7,8,9]);assert.equal(values.same,1);assert.equal(values.ordered,1)
})

test('General tuple values, return inference and unpacking preserve fields and ownership', () => {
    const values=execute(`def pair(int n):
    return n,"value"
p=pair(7)
a,b=p
str text=p[1]
int number=p[0]
a,c=8,9
rows=[("A",1),("B",2)]
int total=0
str last=""
for name,value in rows:
    total+=value
    last=name
for n in (1,2,3):
    total+=n
`,{maxSlices:2000})
    assert.deepEqual(values.p,[7,'value']);assert.equal(values.a,8);assert.equal(values.b,'value')
    assert.equal(values.text,'value');assert.equal(values.number,7);assert.equal(values.c,9)
    assert.equal(values.total,9);assert.equal(values.last,'B')
})

test('Integer hash maps grow, handle colliding buckets and tombstones, and keep insertion order', t => {
    const source=`d={}
for i in range(80):
    d[i*8]=i
for i in range(0,80,3):
    del d[i*8]
d[0]=999
d[8]+=10
int missing=d.get(-1,123)
int popped=d.pop(16)
int fallback=d.pop(-7,456)
int existing=d.setdefault(8,777)
int inserted=d.setdefault(-8,888)
bool present=8 in d
bool absent=-1 not in d
int size=len(d)
int ordered=[k for k in d]
int pairs=[k+v for k,v in d.items()]
int values=[v for v in d.values()]
`
    const measurements={}
    const result=execute(source,measurements)
    const expected=new Map(Array.from({length:80},(_,i)=>[i*8,i]).filter(([key])=>key%24!==0))
    expected.set(0,999);expected.set(8,11);expected.delete(16);expected.set(-8,888)
    assert.deepEqual(result.d,expected)
    assert.equal(result.missing,123);assert.equal(result.popped,2);assert.equal(result.fallback,456)
    assert.equal(result.existing,11);assert.equal(result.inserted,888)
    assert.equal(result.present,1);assert.equal(result.absent,1);assert.equal(result.size,expected.size)
    assert.deepEqual(result.ordered,[...expected.keys()])
    assert.deepEqual(result.pairs,[...expected].map(([k,v])=>k+v))
    assert.deepEqual(result.values,[...expected.values()])
    assert.doesNotMatch(measurements.assembly,/__dict_copy:/)
    t.diagnostic(`Integer hashmap: ${measurements.bytes} bytes`)
})

test('String dictionaries compare contents, own key/value lifetimes and transfer popped values', () => {
    const result=execute(`def identity(str value) -> str:
    return value
d={"Alice":"first","Bob":"second","Alice":"updated"}
str key=identity("Alice")
str read=d[key]
str popped=d.pop("Bob")
str missing=d.get("missing","fallback")
str added=d.setdefault("Carol","third")
str existing=d.setdefault("Alice","unused")
bool found=identity("Alice") in d
str after=d[identity("Carol")]
d.clear()
str still=read
int count=len(d)
`)
    assert.equal(result.read,'updated');assert.equal(result.popped,'second')
    assert.equal(result.missing,'fallback');assert.equal(result.added,'third')
    assert.equal(result.existing,'updated');assert.equal(result.after,'third')
    assert.equal(result.found,1);assert.equal(result.still,'updated');assert.equal(result.count,0)
    assert.deepEqual(result.d,new Map())
    const shared=execute('str value="same"\nd={value:value}\nvalue="changed"\nd["same"]=d["same"]\nstr held=d.pop("same")\nstr fallback=d.pop("missing","fallback")')
    assert.equal(shared.held,'same');assert.equal(shared.fallback,'fallback')
})

test('Dictionary aliases, copies, updates, explicit empty types and recursive return cleanup work', () => {
    const result=execute(`dict[str,int] empty={}
empty["A"]=1
alias=empty
copy=dict(empty)
alias["B"]=2
copy.update({"A":10,"C":3})
copy.update(copy)
def make(int n) -> dict[str,str]:
    local={"key":"value"}
    if n>0:
        return make(n-1)
    return local
dict[str,str] final={}
for i in range(120):
    final=make(3)
str value=final["key"]
int order=[v for k,v in copy.items()]
`,{maxSlices:4000})
    assert.deepEqual(result.empty,new Map([['A',1],['B',2]]))
    assert.deepEqual(result.alias,result.empty)
    assert.deepEqual(result.copy,new Map([['A',10],['C',3]]))
    assert.equal(result.value,'value');assert.deepEqual(result.order,[10,3])
})

test('Dictionary iteration preserves order and detects structural mutation before dereferencing entries', () => {
    const result=execute(`d={1:10,2:20,3:30}
def total(dict[int,int] values) -> int:
    result=0
    for k,v in values.items():
        if k==2:
            continue
        result+=v
    return result
int sum=total(d)
for k in d.keys():
    d[k]+=1
int ordered=[v for v in d.values()]
`)
    assert.equal(result.sum,40);assert.deepEqual(result.ordered,[11,21,31])
    assert.throws(()=>execute('d={1:2}\nfor k in d:\n    d[3]=4'),/changed during iteration/)
    assert.throws(()=>execute('d={1:2}\nint x=d[9]'),/Dictionary key not found/)
    assert.throws(()=>execute('d={1:2}\ndel d[9]'),/Dictionary key not found/)
    assert.throws(()=>execute('d={}\nfor i in range(1000):\n    d[i]=i'),/out of memory/)
})

test('Dictionary render behavior agrees across optimization modes and tombstone reuse stays bounded', () => {
    const source=readFileSync(resolve('../../examples/dictionaries.tip'),'utf8')
    const reference={optimization:'reference'},balanced={optimization:'balanced'},aggressive={optimization:'aggressive'}
    execute(source,reference);execute(source,balanced);execute(source,aggressive)
    assert.deepEqual(aggressive.frames,reference.frames)
    assert.deepEqual(balanced.frames,reference.frames)
    assert.ok(aggressive.screen.some(n=>n))
    const values=execute('d={}\nfor i in range(300):\n    d[i]=i\n    int value=d.pop(i)\nd[0]=7\nint result=d[0]',{maxSlices:3000})
    assert.deepEqual(values.d,new Map([[0,7]]));assert.equal(values.result,7)
})

test('True string hash collisions still distinguish keys, including deletion and reinsertion', () => {
    const hashes=new Map()
    let collision
    for(let i=0;i<100000 && !collision;i++) {
        const key='key'+i
        let h=5381
        for(const c of key) h=((h*33)^c.charCodeAt(0))&65535
        if(hashes.has(h)) collision=[hashes.get(h),key]
        else hashes.set(h,key)
    }
    assert.ok(collision)
    const [a,b]=collision
    const result=execute(`d={"${a}":1,"${b}":2}\nint first=d["${a}"]\nint second=d["${b}"]\nint ha=hash("${a}")\nint hb=hash("${b}")\ndel d["${a}"]\nint retained=d["${b}"]\nd["${a}"]=3`)
    assert.equal(result.ha,result.hb);assert.equal(result.first,1);assert.equal(result.second,2);assert.equal(result.retained,2)
    assert.deepEqual(result.d,new Map([[b,2],[a,3]]))
})

test('Print draws transparent glyph strokes at pixel coordinates with explicit rendering', () => {
    const seed=Uint8Array.from({length:768},()=>0x55)
    const measurements={seedGraph:seed}
    execute('print("A ",3,2)\nrender()',measurements)
    const expected=seed.slice()
    const glyph=['01110','10001','10001','11111','10001','10001','10001']
    glyph.forEach((row,y)=>[...row].forEach((bit,x)=>{if(bit==='1')expected[(y+2)*12+((x+3)>>3)]|=128>>((x+3)&7)}))
    assert.deepEqual(measurements.graph,expected)
    assert.equal(measurements.frames.length,1)
    assert.doesNotMatch(measurements.assembly,/__heap_start|__text_input:/)
    const unrendered={}
    execute('print("Hi",0,0)',unrendered)
    assert.equal(unrendered.frames.length,0)
    for(const number of [0,1,-1,32767,-32768]) {
        const formatted={},literal={}
        execute(`clear()\nprint(${number},0,0)\nrender()`,formatted)
        execute(`clear()\nprint("${number}",0,0)\nrender()`,literal)
        assert.deepEqual(formatted.graph,literal.graph)
    }
    const clipped={}
    execute('print("hello\\nWorld!?",-2,61)\nrender()',clipped)
    assert.ok(clipped.graph.some(n=>n))
})

test('Managed strings survive assignments, calls, returns, replacement and repeated cleanup', () => {
    const values=execute(`str greeting="Hello"
str other=greeting
greeting="Goodbye"
def identity(str value) -> str:
    str copy=value
    return copy
str answer=identity(other)
for i in range(100):
    greeting=identity("again")
int size=len(answer)
print(answer,0,0)
print(True,0,8)
render()`)
    assert.equal(values.other,'Hello')
    assert.equal(values.answer,'Hello')
    assert.equal(values.greeting,'again')
    assert.equal(values.size,5)
})

test('Integer input blinks, preserves the background, edits and returns signed i16', () => {
    const measurements={seedGraph:Uint8Array.from({length:768},(_,i)=>(i*17+83)&255),
        pollKeys:[...Array(55).fill(0),17,0,18,0,26,0,20,0,56,0,28,0,9]}
    const values=execute('int answer=input("",0,0)',measurements)
    assert.equal(values.answer,-328)
    assert.ok(measurements.frames.length>=8)
    const on=measurements.frames[0],off=measurements.frames[1]
    for(let i=0;i<6144;i++) assert.equal(on[i]^off[i],Number(i>=7*96 && i<7*96+5),'Only the cursor stroke changes while blinking')
    const expected={seedGraph:Uint8Array.from({length:768},(_,i)=>(i*17+83)&255)}
    execute('print("-328",0,0)\nrender()',expected)
    assert.deepEqual(measurements.graph,expected.graph,'Final answer removes the cursor and restores all untouched background bits')
    assert.doesNotMatch(measurements.assembly,/__heap_start/)
    const minimum=execute('int answer=input("",0,0)',{pollKeys:[17,0,18,0,26,0,36,0,19,0,28,0,9]})
    assert.equal(minimum.answer,-32768)
    const maximum=execute('int answer=input("",0,0)',{pollKeys:[18,0,26,0,36,0,19,0,28,0,9,0,56,0,36,0,9]})
    assert.equal(maximum.answer,32767,'Out-of-range entry stays editable until corrected')
    assert.throws(()=>execute('int answer=input("",0,63)'),/Input position/)
})

test('Text input uses ALPHA and DEL, keeps earlier answers alive, and handles empty input', () => {
    const measurements={pollKeys:[48,0,47,0,39,0,31,0,56,0,31,0,9,0,34,0,9,0,9]}
    const values=execute('str first=input("Name:",0,0)\nstr second=input("",0,16)\nstr empty=input("",0,32)\nprint(first,0,48)\nrender()',measurements)
    assert.equal(values.first,'ABC')
    assert.equal(values.second,'1')
    assert.equal(values.empty,'')
    assert.doesNotMatch(measurements.assembly,/__text_parse:/)
    const lowercase=execute('str answer=input("",0,0)',{pollKeys:[48,0,48,0,47,0,39,0,31,0,9]})
    assert.equal(lowercase.answer,'abc')
})

test('Tuple rows snapshot values once, unpack in order, and preserve nested control flow and frames', () => {
    const source=`int xs=[10,20,30,40]
int result=0
for a,b in [(xs.pop(),xs.pop()),(xs.pop(),xs.pop())]:
    result+=a-b
int remaining=len(xs)
def nested(n):
    total=0
    for a,b in [(n,n+1),(n+2,n+3)]:
        for c,d in [(1,2),(3,4)]:
            if c==1:
                continue
            total+=a+b+c+d
    return total
int computed=nested(5)
def early(n):
    for a,b in [(n,2),(n+1,3)]:
        return a+b
    return 0
int repeated=0
for i in range(100):
    repeated+=early(3)
int count=0
for a,b in [(1,2),(3,4),(5,6)]:
    count+=1
    break
int empty=7
for a,b in []:
    empty=0
int x=99
int sums=[x+y for x,y in [(1,2),(3,4)]]
`
    for(const optimization of ['reference','balanced']) {
        const values=execute(source,{optimization})
        assert.equal(values.result,20)
        assert.equal(values.remaining,0)
        assert.equal(values.computed,40)
        assert.equal(values.repeated,500)
        assert.equal(values.count,1)
        assert.equal(values.empty,7)
        assert.equal(values.x,99)
        assert.deepEqual(values.sums,[3,7])
    }
    const staticRows={}
    execute('clear()\nfor x,y in [(0,0),(95,63)]:\n    pixel_on(x,y)\nrender()',staticRows)
    assert.doesNotMatch(staticRows.assembly,/__heap_start/)
    assert.equal(staticRows.screen[0],1)
    assert.equal(staticRows.screen[6143],1)
    const folded={optimization:'aggressive'}
    execute('clear()\nfor x,y in [(0,0),(95,63)]:\n    pixel_on(x,y)\nrender()',folded)
    assert.deepEqual(folded.frames,staticRows.frames)
})

test('Digital rain runs the complete random comprehensions and tuple drawing loop natively', t => {
    const source=readFileSync(resolve('../../examples/digital_rain.tip'),'utf8')
    const compiled=spawnSync(process.env.TIPY_PYTHON || 'python',['-m','tipy','-','--show','all'],{
        cwd:resolve('../..'),input:source,encoding:'utf8'
    })
    assert.equal(compiled.status,0,compiled.stderr)
    const result=JSON.parse(compiled.stdout)
    const runner=new Ti84Runner(result.assembly)
    const frames=[]
    const service=runner.serviceBcall.bind(runner)
    runner.lcd.dirty=false
    runner.serviceBcall=()=>{
        service()
        if(runner.lcd.dirty) { frames.push(runner.lcd.pixels.slice()); runner.lcd.dirty=false }
    }
    for(let slices=0;slices<2500 && frames.length<35 && !runner.done;slices++) runner.runSlice()
    assert.equal(runner.done,false)
    assert.ok(frames.length>=35)
    assert.ok(new Set(frames.map(f=>Buffer.from(f).toString('base64'))).size>=25)
    assert.ok(frames[30].some(n=>n))
    assert.ok(runner.machineBytes<25193)
    assert.doesNotMatch(result.assembly,/__native_sin/)
    t.diagnostic(`Digital rain: ${runner.machineBytes} bytes, ${frames.length} native frames`)
})

test('Native fixed-point trig, conversions, inference and chained comparison semantics', () => {
    const source = `import math
a = math.radians(90)
b = int(math.sin(a) * 20)
c = int(math.cos(a) * 20)
d = int(-1.75)
e = int(1.75)
f = int(math.sin(math.radians(270)) * 20)
g = int(40 + math.cos(math.radians(180)) * 12)
int xs=[9,3]
bool once = 0 < xs.pop() < 5
bool short = 10 < 5 < xs.pop()
int remaining = len(xs)
def add(a,b):
    total=a+b
    return total
h=add(3,4)
`
    for (const optimization of ['reference','balanced']) {
        const values = execute(source,{optimization})
        assert.equal(values.b,20)
        assert.equal(values.c,0)
        assert.equal(values.d,-1)
        assert.equal(values.e,1)
        assert.equal(values.f,-20)
        assert.equal(values.g,28)
        assert.equal(values.once,1)
        assert.equal(values.short,0)
        assert.equal(values.remaining,1)
        assert.equal(values.h,7)
    }
})

test('Native randint stays within inclusive bounds and preserves runtime effects', () => {
    const source = `import random
random.seed(123)
int samples=[]
for n in range(200):
    samples.append(random.randint(-3,3))
int exact=random.randint(-32768,-32768)
int full=random.randint(-32768,32767)
int high=random.randint(32767,32767)
`
    for (const optimization of ['reference','balanced']) {
        const values=execute(source,{optimization})
        assert.equal(values.samples.length,200)
        assert.ok(values.samples.every(n=>n>=-3 && n<=3))
        assert.equal(new Set(values.samples).size,7)
        assert.equal(values.exact,-32768)
        assert.equal(values.high,32767)
    }
    const effect={optimization:'aggressive'}
    execute('import random\nimport time\nclear()\npixel_on(random.randint(0,95),0)\nrender()\ntime.sleep(0.04)',effect)
    assert.match(effect.assembly,/__native_randint:/)
    assert.match(effect.assembly,/__native_sleep:/)
    assert.equal(effect.frames.length,1,'Sleep must not implicitly render')
    assert.ok(effect.screen.some(n=>n))
})

test('Sleep uses milliseconds, does not render, and respects dynamic fixed-point seconds', () => {
    for(const source of ['time.sleep(0.04)','s=0.04\ntime.sleep(s)','s=0\ntime.sleep(s)']) {
        const measurements={}
        execute('import time\n'+source,measurements)
        assert.equal(measurements.frames.length,0)
        if(!source.includes('s=0\n')) assert.ok(measurements.tStates>=230000 && measurements.tStates<260000)
    }
    assert.throws(()=>execute('import random\nx=random.randint(5,4)'),/randint|Runtime error/)
    assert.throws(()=>execute('import time\ns=-1\ntime.sleep(s)'),/sleep|Runtime error/)
})

test('Full firework animation runs natively across repeated draw-render-sleep frames', t => {
    const source=readFileSync(resolve('../../examples/fireworks.tip'),'utf8')
    const compiled=spawnSync(process.env.TIPY_PYTHON || 'python',['-m','tipy','-','--show','all'],{
        cwd:resolve('../..'),input:source,encoding:'utf8'
    })
    assert.equal(compiled.status,0,compiled.stderr)
    const result=JSON.parse(compiled.stdout)
    const runner=new Ti84Runner(result.assembly)
    const frames=[]
    const service=runner.serviceBcall.bind(runner)
    runner.lcd.dirty=false
    runner.serviceBcall=()=>{
        service()
        if(runner.lcd.dirty) { frames.push(runner.lcd.pixels.slice()); runner.lcd.dirty=false }
    }
    for(let slices=0; slices<2000 && frames.length<25 && !runner.done; slices++) runner.runSlice()
    assert.equal(runner.done,false,'The animation loop keeps running')
    assert.ok(frames.length>=25,'Must finish an explosion and start another')
    assert.ok(frames[0].some(n=>n))
    assert.ok(frames[22].every(n=>!n),'Explicit clear/render creates an empty frame between fireworks')
    assert.ok(frames[23].some(n=>n))
    assert.ok(new Set(frames.map(f=>Buffer.from(f).toString('base64'))).size>=20)
    assert.equal(runner.heapBytes,0)
    assert.ok(runner.machineBytes<10000)
    t.diagnostic(`Fireworks: ${runner.machineBytes} bytes, ${frames.length} native frames, ${runner.machine.tStateCount} T-states`)
})

test('Compiler batches checkerboard pixel loops into reusable masks without changing program state', t => {
    const source = 'def get_evens(int[] x) -> int[]:\n    int result=[]\n    for n in x:\n        if n%2==0:\n            result.append(n)\n    return result\ndef get_odds(int[] x) -> int[]:\n    int result=[]\n    for n in x:\n        if n%2!=0:\n            result.append(n)\n    return result\nint x=list(range(96))\nint evens=get_evens(x)\nint odds=get_odds(x)\nclear()\nfor row in range(64):\n    if row%2==0:\n        for pixel in evens:\n            pixel_on(pixel,row)\n    elif row%2!=0:\n        for pixel in odds:\n            pixel_on(pixel,row)\nrender()'
    const before={optimizeGraphics:false},after={}
    assert.deepEqual(execute(source,after),execute(source,before))
    assert.deepEqual(after.screen,before.screen)
    assert.deepEqual(after.graph,before.graph)
    assert.ok(after.tStates<before.tStates*0.5,'Row batching should cut cycle count by at least half')
    assert.deepEqual(Array.from(after.graph),Array.from({length:768},(_,i)=>Math.floor(i/12)%2?0x55:0xaa))
    t.diagnostic(`Checkerboard: ${before.tStates} -> ${after.tStates} T-states; ${before.bytes} -> ${after.bytes} bytes`)
    const aggressive={optimization:'aggressive'}
    assert.deepEqual(execute(source,aggressive),{})
    assert.deepEqual(aggressive.frames,before.frames)
    assert.deepEqual(aggressive.graph,before.graph)
    assert.ok(aggressive.bytes<100,'Static checkerboard should be a tiny pattern loader with no list heap')
    assert.ok(aggressive.tStates<100000)
    t.diagnostic(`Aggressive checkerboard: ${aggressive.bytes} bytes, ${aggressive.tStates} T-states`)
})

test('Row batching preserves clipping, duplicates, pixel modes, empty sources and repeated function calls', () => {
    const cases = [
        'int xs=[-1,0,0,1,7,8,95,96,256]\nclear()\nfor row in range(-1,66):\n    for x in xs:\n        flip_pixel(x,row)\nrender()',
        'int a=[0,1,1,2]\nint b=[1,2,3]\nclear()\nfor row in range(64):\n    for x in a:\n        pixel_on(x,row)\n    for x in b:\n        pixel_off(x,row)\n    for x in a:\n        flip_pixel(x,row)\nrender()',
        'int xs=[]\nint x=42\nclear()\nfor row in range(2):\n    for x in xs:\n        pixel_on(x,row)\nrender()',
        'int xs=[]\nint x=42\nclear()\nfor row in range(64):\n    for x in xs:\n        pixel_on(x,row)\nrender()',
        'int xs=[1,7]\nclear()\npixel_on(50,10)\nfor row in range(64):\n    for x in xs:\n        pixel_on(x,row)\nrender()',
        'def draw(int[] xs) -> int:\n    int x=-1\n    for row in range(64):\n        for x in xs:\n            pixel_on(x,row)\n    return x\nint xs=[1,5,9]\nclear()\nint last=draw(xs)\nxs.append(12)\nlast=draw(xs)\nrender()'
    ]
    for(const source of cases) {
        const before={optimizeGraphics:false},after={}
        assert.deepEqual(execute(source,after),execute(source,before))
        assert.deepEqual(after.graph,before.graph)
        assert.deepEqual(after.screen,before.screen)
    }
})

test('Aggressive specialization preserves every rendered frame and unknown initial framebuffer bits', () => {
    const seed=Uint8Array.from({length:768},(_,i)=>(i*37+91)&255)
    const sources=[
        'pixel_on(1,1)\npixel_off(2,1)\nflip_pixel(3,1)\nrender()\nflip_pixel(3,1)\nrender()',
        'clear()\nrectangle(10,50,10,50,1,False)\nrender()\nline(-10,63,105,0)\nrender()\nclear()\nrender()',
        'int a=[1,2]\na[-1]+=a.pop()\nclear()\npixel_on(a[0],0)\nrender()',
        'def identity(int[] x) -> int[]:\n    return x\nint a=[1]\nint b=identity(a)\nb.append(7)\nclear()\nfor n in a:\n    pixel_on(n,0)\nrender()',
        'int xs=[0,1,2]\nclear()\nfor i,x in enumerate(xs):\n    if i==0:\n        xs.append(3)\n    pixel_on(x,i)\nrender()',
        'def f(int a,int b) -> int:\n    return a*10+b\nint xs=[1,2]\nint value=f(xs.pop(),len(xs))\nclear()\npixel_on(value,0)\nrender()',
        'clear()\nrender()\npixel_on(9,9)',
    ]
    for(const source of sources) {
        const reference={seedGraph:seed},aggressive={optimization:'aggressive',seedGraph:seed}
        execute(source,reference); execute(source,aggressive)
        assert.deepEqual(aggressive.frames,reference.frames)
        assert.deepEqual(aggressive.screen,reference.screen)
        assert.doesNotMatch(aggressive.assembly,/__heap_start/)
    }
})

test('Balanced constant folding preserves aliases, argument effects and runtime list mutability', () => {
    const sources=[
        'def identity(int[] x) -> int[]:\n    return x\nint a=[1]\nint b=identity(a)\nb.append(7)\nint c=list(identity(a))\nc.append(9)',
        'def f(int a,int b) -> int:\n    return a*10+b\nint xs=[1,2]\nint value=f(xs.pop(),len(xs))',
        'def evens(int[] xs) -> int[]:\n    int result=[]\n    for n in xs:\n        if n%2==0:\n            result.append(n)\n    return result\nint values=evens(list(range(30)))\nvalues.append(42)',
        'def identity(int[] x) -> int[]:\n    return x\ndef make() -> int[]:\n    int local=[1]\n    int alias=identity(local)\n    alias.append(3)\n    render()\n    return alias\ndef other() -> int:\n    u8 x=0\n    return x\nint b=make()\nint n=other()',
        'def touch(int[] a) -> int:\n    a.append(5)\n    int old=a.pop()\n    return 7\nint a=[1]\nint result=touch(a)',
    ]
    for(const source of sources) assert.deepEqual(execute(source,{optimization:'balanced'}),execute(source))
})

test('Aggressive arithmetic evaluation matches Z80 wrapping, division and boolean semantics', () => {
    const source='def encode(int value,int row) -> void:\n    int bit=0\n    while bit<16:\n        if value%2!=0:\n            pixel_on(bit,row)\n        value//=2\n        bit+=1\nint a=[7,-7,-32768,32767]\nint values=[a[0]*-3,a[1]//3,a[1]%3,a[0]//-3,abs(a[2]),sum(range(10)),min(a),max(a)]\nclear()\nfor row,value in enumerate(values):\n    encode(value,row)\nrender()'
    const reference={},aggressive={optimization:'aggressive'}
    execute(source,reference); execute(source,aggressive)
    assert.deepEqual(aggressive.frames,reference.frames)
    assert.doesNotMatch(aggressive.assembly,/__math_/)
    const boundaries='def encode(int value,int row) -> void:\n    int bit=0\n    while bit<16:\n        if value%2!=0:\n            pixel_on(bit,row)\n        value//=2\n        bit+=1\nint a=[-32768,32767,-1,32767,0,-32768,12345,-21017]\nint b=[-1,1,32767,-32768,-1,-32768,-17,9]\nint values=[]\nfor x,y in zip(a,b):\n    values.append(x*y)\n    values.append(x//y)\n    values.append(x%y)\nclear()\nfor row,value in enumerate(values):\n    encode(value,row)\nrender()'
    const before={maxSlices:1000},after={optimization:'aggressive'}
    execute(boundaries,before); execute(boundaries,after)
    assert.deepEqual(after.frames,before.frames)
})

test('Aggressive optimization preserves runtime failures and falls back for long observable sequences', () => {
    for(const source of ['int x=[1]\nint y=x[5]','int z=0\nint x=1//z','int x=min([])','int step=0\nfor i in range(4,8,step):\n    pass']) {
        let error
        try {execute(source)} catch(e) {error=e.message}
        assert.ok(error)
        assert.throws(()=>execute(source,{optimization:'aggressive'}), e=>e.message===error)
    }
    const reference={},aggressive={optimization:'aggressive'}
    const source='for i in range(65):\n    render()'
    execute(source,reference); execute(source,aggressive)
    assert.equal(aggressive.frames.length,65)
    assert.deepEqual(aggressive.frames,reference.frames)
    const conditional='def draw(bool flag) -> void:\n    clear()\n    if flag:\n        pixel_on(1,1)\n    clear()\n    render()\nfor i in range(65):\n    draw(True)'
    const expected={},balanced={optimization:'balanced'},fallback={optimization:'aggressive'}
    execute(conditional,expected); execute(conditional,balanced); execute(conditional,fallback)
    assert.deepEqual(balanced.frames,expected.frames)
    assert.deepEqual(fallback.frames,expected.frames)
    const inline='def inc(int x) -> int:\n    int unused=17\n    return x+1\nfor i in range(65):\n    clear()\n    pixel_on(inc(i),0)\n    render()'
    const original={},optimized={optimization:'aggressive'}
    execute(inline,original); execute(inline,optimized)
    assert.deepEqual(optimized.frames,original.frames)
    assert.doesNotMatch(optimized.assembly,/__fn_/)
    assert.ok(optimized.tStates<original.tStates)
})

test('get_evens returns an owned list that survives function cleanup', () => {
    const source = 'def get_evens(int[] x) -> int[]:\n    int evens=[]\n    for num in x:\n        if num%2==0:\n            evens.append(num)\n    return evens\nint x=[1,2,3,4,5]\nint evens=get_evens(x)'
    const measurements = {}
    assert.deepEqual(execute(source, measurements), { x: [1,2,3,4,5], evens: [2,4] })
    assert.ok(measurements.bytes < 10192, 'Code/data should stay under 2,000 bytes plus the 8 KiB heap')
    assert.ok(measurements.tStates < 45000, 'Filtering should stay below the previous 53,055 T-state baseline')
    assert.deepEqual(execute(source.replace('[1,2,3,4,5]', '[-4,-3,-2,-1,0,1,2]')), {
        x: [-4,-3,-2,-1,0,1,2], evens: [-4,-2,0,2]
    })
})

test('Native pixel on/off/flip update RAM bits and the actual LCD at screen boundaries', () => {
    const measurements = {}
    assert.deepEqual(execute('clear()\npixel_on(0,0)\npixel_on(7,0)\npixel_on(8,0)\npixel_on(95,63)\npixel_off(7,0)\nflip_pixel(8,0)\nflip_pixel(94,63)\npixel_on(1,1)\npixel_on(1,1)\nflip_pixel(2,2)\nflip_pixel(2,2)\nrender()', measurements), {})
    assert.deepEqual(Array.from(measurements.screen.entries()).filter(([,on]) => on).map(([index]) => index),
        [0,97,63*96+94,63*96+95])
    assert.equal(measurements.graph[0], 0x80)
    assert.equal(measurements.graph[1], 0)
    assert.equal(measurements.graph[767], 3)
})

test('Pixel coordinates keep full i16 range, evaluate once in order, and preserve caller locals', () => {
    const measurements = {}
    const source = 'def x() -> int:\n    pixel_on(1,1)\n    return 12\ndef y() -> int:\n    pixel_off(1,1)\n    return 13\ndef draw(int a,int b) -> int:\n    int local=1234\n    pixel_on(a,b)\n    flip_pixel(a,b)\n    pixel_on(x(),y())\n    return local\nint result=draw(20,30)\npixel_on(-1,0)\npixel_on(0,-1)\npixel_on(96,0)\npixel_on(0,64)\npixel_on(256,0)\npixel_on(0,256)\nflip_pixel(-32768,32767)'
    assert.deepEqual(execute(source + '\nrender()', measurements), {result:1234})
    assert.deepEqual(Array.from(measurements.screen.entries()).filter(([,on]) => on).map(([index]) => index), [13*96+12])
})

test('Drawing and clear only change the buffer; render is the explicit LCD boundary', () => {
    const m = {}
    execute('pixel_on(1,1)', m)
    assert.equal(m.graph[12], 64)
    assert.equal(m.screen.some(Boolean), false)
    execute('pixel_on(1,1)\nrender()\nclear()', m)
    assert.equal(m.screen[97], 1)
    assert.equal(m.graph.some(Boolean), false)
    execute('pixel_on(1,1)\nrender()\nclear()\nrender()', m)
    assert.equal(m.screen.some(Boolean), false)
})

function activePixels(screen) {
    return Array.from(screen.entries()).filter(([,on]) => on).map(([index]) => index)
}

test('Python range/list constructors, copies and aggregate builtins execute natively', () => {
    assert.deepEqual(execute('int x=list(range(0,30))\nint y=range(5,-3,-2)\nint z=list(x)\nz[0]=99\nint total=sum(x)\nint started=sum(y,10)\nint low=min(y)\nint high=max(y)\nint scalar=min(9,-2,3)\nint scalarMax=max(-4,5,-1)\nint absolute=abs(-7)\nbool some=any(x)\nbool every=all(x)\nbool vacuous=all([])\nbool empty=any([])'), {
        x:Array.from({length:30},(_,i)=>i),y:[5,3,1,-1],z:[99,...Array.from({length:29},(_,i)=>i+1)],
        total:435,started:18,low:-1,high:5,scalar:-2,scalarMax:5,absolute:7,some:1,every:0,vacuous:1,empty:0
    })
    assert.deepEqual(execute('int a=range(32766,32767,10)\nint b=range(-32767,-32768,-10)\nint c=range(32767,-32768,-32768)\nint d=range(0)\nint e=list()'), {
        a:[32766],b:[-32767],c:[32767,-1],d:[],e:[]
    })
    assert.throws(()=>execute('int step=0\nint a=range(0,4,step)'), /step cannot be zero/)
    assert.throws(()=>execute('int a=min([])'), /non-empty sequence/)
    assert.deepEqual(execute('bool a=any([0,1])\nbool b=all([True,False])\nint c=sum([True,False,True])\nbool d=min([True,False])'), {a:1,b:0,c:2,d:0})
})

test('Enumerate and zip evaluate sources once, stop at shortest input, and preserve per-call frames', () => {
    assert.deepEqual(execute('def count() -> int:\n    int total=0\n    for i,value in enumerate(range(4),5):\n        total+=i+value\n    for a,b in zip([1,2,3],[10,20]):\n        total+=a+b\n    return total\nint result=count()\nint i=0\nwhile i<100:\n    result=count()\n    i+=1'), {result:65,i:100})
    assert.deepEqual(execute('int x=[1,2,3]\nint y=[4,5,6]\nint total=0\nfor a,b in zip(x,y):\n    if a==1:\n        continue\n    total+=a+b\n    break\nfor i,value in enumerate(x,-2):\n    total+=i'), {x:[1,2,3],y:[4,5,6],total:4,a:2,b:5,i:0,value:3})
})

test('Direct range loops avoid heap allocation and handle boundary steps and target rebinding', () => {
    const m={}
    assert.deepEqual(execute('int total=0\nfor i in range(10):\n    total+=i',m), {total:45,i:9})
    assert.ok(m.bytes<1000, 'A counter loop must not add the 8 KiB list heap')
    assert.deepEqual(execute('def collect(int start,int stop,int step) -> int[]:\n    int result=[]\n    for n in range(start,stop,step):\n        result.append(n)\n        n=100\n    return result\nint a=collect(32766,32767,10)\nint b=collect(-32767,-32768,-10)\nint c=collect(32767,-32768,-32768)\nint d=collect(0,0,1)\nint e=collect(5,-3,-2)'), {
        a:[32766],b:[-32767],c:[32767,-1],d:[],e:[5,3,1,-1]
    })
    assert.deepEqual(execute('int total=0\nfor i in range(0,10,2):\n    if i==2:\n        continue\n    if i==6:\n        break\n    total+=i'), {total:4,i:6})
    assert.throws(()=>execute('int step=0\nfor i in range(4,8,step):\n    pass'), /step cannot be zero/)
})

test('Membership preserves argument order and temporary ownership', () => {
    assert.deepEqual(execute('int values=[1,2,3]\nbool a=2 in values\nbool b=7 not in values\nbool c=9 in range(10)\nbool d=0 in []\nbool e=True in [False,True]\nbool f=not 1 in values'), {
        values:[1,2,3],a:1,b:1,c:1,d:0,e:1,f:0
    })
})

test('List comprehensions filter, nest, unpack iterators, capture outer values, and keep targets private', () => {
    assert.deepEqual(execute('int n=99\nint evens=[n for n in range(6) if n%2==0]\nint squares=[n*n for n in range(4)]\nint pairs=[a+b for a,b in zip([1,2],[10,20,30])]\nint matrix=[a*10+b for a in range(2) for b in range(3) if not b==1]\nint indexed=[i+v for i,v in enumerate([10,20],5)]\nint nested=[sum([n*m for m in range(3)]) for n in range(3)]\nbool flags=[v>0 for v in [-1,0,1]]'), {
        n:99,evens:[0,2,4],squares:[0,1,4,9],pairs:[11,22],matrix:[0,2,10,12],indexed:[15,26],nested:[0,3,6],flags:[0,0,1]
    })
})

test('Comprehensions inside recursive/repeated functions release hidden iterator references', () => {
    assert.deepEqual(execute('def f(int[] x) -> int[]:\n    int n=12\n    int a=[n*2 for n in x if n>0]\n    int b=[m+v for m in [1,2] for v in x]\n    return a\nint x=[-1,0,1,2]\nint result=f(x)\nint i=0\nwhile i<200:\n    result=f(x)\n    i+=1'), {x:[-1,0,1,2],result:[2,4],i:200})
})

test('Multiplication, signed floor division, compound assignments and short-circuit bool operators work', () => {
    assert.deepEqual(execute('def unsafe() -> bool:\n    int zero=0\n    int bad=1//zero\n    return True\nint a=7*-3\nint b=-7//3\nint c=7//-3\nint d=-7//-3\nint e=20\ne*=3\ne//=7\ne%=3\nint values=[7]\nvalues[0]*=4\nvalues[0]//=3\nvalues[0]%=4\nbool first=False and unsafe()\nbool second=True or unsafe()\nbool third=not 3<2 and True\nint branch=0\nif first:\n    branch=1\nelif third:\n    branch=2\nelse:\n    branch=3'), {
        a:-21,b:-3,c:-3,d:2,e:2,values:[1],first:0,second:1,third:1,branch:2
    })
})

test('Lines rasterize every octant, reversed/degenerate endpoints and clipped portions', () => {
    const cases = [[0,0,95,63],[95,0,0,63],[48,32,52,50],[48,32,44,50],
        [48,32,44,14],[48,32,52,14],[95,63,0,0],[4,5,4,5],[-10,-10,10,10],
        [-10,63,105,0],[0,31,95,31],[20,63,20,0],[-32768,-1,32767,-1]]
    for (const [x1,y1,x2,y2] of cases) {
        let a=x1,b=y1,c=x2,d=y2
        const swap=Math.abs(y2-y1)>Math.abs(x2-x1)
        if (swap) [a,b,c,d]=[b,a,d,c]
        const dx=Math.abs(c-a),dy=Math.abs(d-b),sx=a<c?1:-1,sy=b<d?1:-1
        const pixels=new Set()
        // Entirely rejected cases need no long reference traversal.
        if (!(Math.max(x1,x2)<0 || Math.min(x1,x2)>=96 || Math.max(y1,y2)<0 || Math.min(y1,y2)>=64)) {
            for (let step=0;step<=dx;step++) {
                const major=a+sx*step
                const minor=b+sy*(dx?Math.floor((Math.floor(dx/2)+step*dy)/dx):0)
                const [x,y]=swap?[minor,major]:[major,minor]
                if(x>=0&&x<96&&y>=0&&y<64) pixels.add(y*96+x)
            }
        }
        const m={}
        execute(`clear()\nline(${x1},${y1},${x2},${y2})\nrender()`,m)
        assert.deepEqual(activePixels(m.screen),[...pixels].sort((a,b)=>a-b),JSON.stringify([x1,y1,x2,y2]))
    }
})

test('Axis lines clip wide/negative endpoints and reject misaligned endpoints', () => {
    const m={}
    execute('clear()\nh_line(-32768,2,32767,2)\nv_line(3,-32768,3,32767)\nh_line(10,4,2,4)\nv_line(8,8,8,5)\nh_line(0,7,10,8)\nv_line(20,0,21,10)\nrender()',m)
    const expected=new Set()
    for(let x=0;x<96;x++)expected.add(2*96+x)
    for(let y=0;y<64;y++)expected.add(y*96+3)
    for(let x=2;x<=10;x++)expected.add(4*96+x)
    for(let y=5;y<=8;y++)expected.add(y*96+8)
    assert.deepEqual(activePixels(m.screen),[...expected].sort((a,b)=>a-b))
})

test('General line error accumulation handles the entire signed i16 coordinate span', () => {
    const m={maxSlices:2000}
    execute('clear()\nline(-32768,-32768,32767,32767)\nrender()',m)
    assert.deepEqual(activePixels(m.screen),Array.from({length:64},(_,i)=>i*96+i))
})

test('Rectangles support inclusive/reversed corners, thickness, filling, clipping and i16 extremes', () => {
    const cases=[[2,8,3,9,1,false],[8,2,9,3,2,false],[2,8,3,9,0,true],
        [-4,6,-3,7,2,false],[-10,105,-10,75,3,false],[1,3,2,4,30,false],
        [2,8,3,9,0,false],[-32768,32767,-32768,32767,32767,false]]
    for(const [x1,x2,y1,y2,width,filled] of cases) {
        const expected=[]
        const left=Math.min(x1,x2),right=Math.max(x1,x2),top=Math.min(y1,y2),bottom=Math.max(y1,y2)
        for(let y=0;y<64;y++)for(let x=0;x<96;x++) {
            if(x>=left&&x<=right&&y>=top&&y<=bottom&&(filled||(width>0&&Math.min(x-left,right-x,y-top,bottom-y)<width)))expected.push(y*96+x)
        }
        const m={}
        execute(`clear()\nrectangle(${x1},${x2},${y1},${y2},${width},${filled?'True':'False'})\nrender()`,m)
        assert.deepEqual(activePixels(m.screen),expected,JSON.stringify([x1,x2,y1,y2,width,filled]))
    }
})

test('Signed remainder agrees with Python for either divisor sign and i16 boundaries', () => {
    const pairs = [[7,3],[-7,3],[7,-3],[-7,-3],[-32768,-1],[-32768,32767],
        [32767,-32768],[-32768,-32768],[0,-1],[32767,1],[-32768,1]]
    const lines = []
    const expected = {}
    pairs.forEach(([left,right], i) => {
        lines.push(`int a${i}=${left}`, `int b${i}=${right}`, `int r${i}=a${i}%b${i}`)
        expected[`a${i}`]=left
        expected[`b${i}`]=right
        expected[`r${i}`]=((left % right) + right) % right || 0
    })
    lines.push('int precedence=1+7%3-2', 'int signed=(-3)%2')
    expected.precedence=0
    expected.signed=1
    assert.deepEqual(execute(lines.join('\n')),expected)
    assert.throws(() => execute('int zero=0\nint result=7%zero'), /divisor cannot be zero/)
})

test('Returned arrays support aliases, temporaries, recursion, and repeated cleanup', () => {
    assert.deepEqual(execute('def identity(int[] x) -> int[]:\n    return x\ndef make(int n) -> int[]:\n    if n>0:\n        return make(n-1)\n    return [8,9]\nint a=[1]\nint b=identity(a)\nb.append(2)\nint c=make(3)\nint i=0\nwhile i<150:\n    c=identity(make(2))\n    i+=1\nint size=len(identity([1,2,3]))'), {
        a: [1,2], b: [1,2], c: [8,9], i: 150, size: 3
    })
    assert.deepEqual(execute('def bytes() -> u8[]:\n    return [0,255]\ndef flags() -> bool[]:\n    return [True,False]\nu8 a=bytes()\nbool b=flags()'), {a:[0,255], b:[1,0]})
})

test('Python-generated Z80 computes nested arithmetic and updates A without clobbering B or C',() => {
    assert.deepEqual(execute('int B=3\nint C=4\nint A=B+C\nA=A+(B-C)\nA+=1'),{ B: 3, C: 4, A: 7 })
})
test('Python-generated Z80 preserves operand order for nested subtraction',() => {
    assert.deepEqual(execute('int A=20-(4-(3+2))\nint B=1-(2-(3-4))'),{ A: 21, B: -2 })
})
test('Python-generated Z80 handles byte loads, bool copies, signed minimum and distinct case-sensitive names',() => {
    assert.deepEqual(execute('u8 A=255\ni16 a=A\nint low=-32768\nbool flag=True\nbool copy=flag\nA=7'),{ A: 7, a: 255, low: -32768, flag: 1, copy: 1 })
})

test('Python signed comparisons and if/else agree with all boundary/overflow cases',() => {
    const pairs = [[20,10],[10,10],[0,10],[-32765,10],[32767,-10],[-32768,32767],[32767,-32768],[-1,0],[0,-1],[-32768,-32768]]
    const operations = { '>': (a,b) => a>b, '<': (a,b) => a<b, '>=': (a,b) => a>=b, '<=': (a,b) => a<=b, '==': (a,b) => a===b, '!=': (a,b) => a!==b }
    const lines = []
    const expected = {}
    let index = 0
    for (const [a,b] of pairs) {
        const left = `A${index}`
        const right = `B${index}`
        lines.push(`int ${left}=${a}`,`int ${right}=${b}`)
        expected[left] = a
        expected[right] = b
        for (const [operator,compare] of Object.entries(operations)) {
            const name = `R${index++}`
            const flag = `F${index}`
            lines.push(`int ${name}=0`,`if ${left} ${operator} ${right}:`,`    ${name}=1`,'else:',`    ${name}=2`,`bool ${flag}=(${left} ${operator} ${right})`)
            expected[name] = compare(a,b) ? 1 : 2
            expected[flag] = compare(a,b) ? 1 : 0
        }
    }
    assert.deepEqual(execute(lines.join('\n')),expected)
})
test('Python nested branches and bool equality execute without falling through into else',() => {
    assert.deepEqual(execute('int A=20\nbool ready=True\nif ready==True:\n    if A>10:\n        A=0\n    else:\n        A=1\nelse:\n    A=2'),{ A: 0, ready: 1 })
})

test('Python while rechecks changing signed and boolean conditions, including zero iterations',() => {
    assert.deepEqual(execute('int A=-3\nint count=0\nwhile A<3:\n    A+=1\n    count+=1\nwhile False:\n    count=99\nbool ready=True\nwhile ready:\n    count+=1\n    ready=False'),{ A: 3, count: 7, ready: 0 })
})
test('Python continue rechecks the condition and break exits the current loop',() => {
    assert.deepEqual(execute('int A=0\nint total=0\nwhile True:\n    A+=1\n    if A<3:\n        continue\n    if A==5:\n        break\n    total+=A'),{ A: 5, total: 7 })
})
test('Python nested loops target their own labels for break and continue',() => {
    assert.deepEqual(execute('int outer=0\nint inner=0\nint total=0\nwhile outer<3:\n    outer+=1\n    inner=0\n    while inner<5:\n        inner+=1\n        if inner==2:\n            continue\n        if inner==4:\n            break\n        total+=1\n    if outer==2:\n        continue\n    total+=10'),{ outer: 3, inner: 4, total: 26 })
})

test('Functions pass copies, preserve global x/y, and clean up repeated calls',() => {
    assert.deepEqual(execute('int x=3\nint y=4\ndef add(int x,int y) -> int:\n    x+=10\n    int result=x+y\n    return result\nint A=add(x,y)\nint B=add(20,2)'),{ x: 3, y: 4, A: 17, B: 32 })
})
test('Nested calls preserve operand order, intermediate results and caller locals',() => {
    assert.deepEqual(execute('def sub(int a,int b) -> int:\n    return a-b\ndef outer(int x) -> int:\n    int saved=x+10\n    int answer=sub(sub(x,2),sub(9,3))\n    return saved+answer\nint A=outer(20)\nint B=100-sub(9,2)\nint C=sub(1,sub(8,3))'),{ A: 42, B: 93, C: -4 })
})
test('Recursive functions give each invocation its own locals',() => {
    assert.deepEqual(execute('def sum(int n) -> int:\n    int saved=n\n    if n<=0:\n        return 0\n    int nested=sum(n-1)\n    return saved+nested\nint A=sum(10)\ndef fib(int n) -> int:\n    if n<2:\n        return n\n    return fib(n-1)+fib(n-2)\nint B=fib(7)'),{ A: 55, B: 13 })
})
test('Mutual recursion, bool/byte parameters, void calls and early loop returns work',() => {
    assert.deepEqual(execute('def even(int n) -> bool:\n    if n==0:\n        return True\n    return odd(n-1)\ndef odd(int n) -> bool:\n    if n==0:\n        return False\n    return even(n-1)\ndef byte(u8 b) -> u8:\n    return b\ndef noop(bool flag) -> void:\n    if flag:\n        return\n    pass\ndef count(int n) -> int:\n    int result=0\n    while n>0:\n        n-=1\n        if n==2:\n            return result\n        result+=1\n    return result\nbool A=even(8)\nbool B=odd(8)\nu8 C=byte(255)\nnoop(True)\nnoop(False)\nint D=count(5)'),{ A: 1, B: 0, C: 255, D: 2 })
})
test('Local names shadow globals without affecting them; safe global reads work',() => {
    assert.deepEqual(execute('int x=100\nint base=7\ndef f() -> int:\n    int x=3\n    return x+base\nint A=f()'),{ x: 100, base: 7, A: 10 })
})

test('Many repeated calls do not leak stack space',() => {
    assert.deepEqual(execute('def step(int x) -> int:\n    int copy=x\n    return copy\nint i=0\nint total=0\nwhile i<100:\n    total+=step(i)\n    i+=1'),{ i: 100, total: 4950 })
})
test('Frame-relative loads/stores work at the supported displacement boundaries',() => {
    const parameters = Array.from({ length: 62 },(_,i) => `int A${i}`).join(',')
    const arguments_ = Array.from({ length: 62 },(_,i) => i).join(',')
    const locals = Array.from({ length: 64 },(_,i) => `    int local${i}=${i}`).join('\n')
    assert.deepEqual(execute(`def wide(${parameters}) -> int:\n${locals}\n    return A0+A61+local63\nint result=wide(${arguments_})`),{ result: 124 })
})

test('Typed arrays grow, shift, index and retain constant-time length metadata',() => {
    assert.deepEqual(execute('int scores=[10,20,30]\nint x=scores[1]\nscores[2]=40\nscores.append(50)\nscores.insert(1,15)\nint last=scores.pop()\nint first=scores.pop(0)\nscores.remove(20)\nint size=len(scores)'),{ scores: [15,40], x: 20, last: 50, first: 10, size: 2 })
})

test('Byte/bool arrays, negative indices, clamped insertion and augmented assignment work',() => {
    assert.deepEqual(execute('u8 bytes=[]\nbytes.extend([1,255,7])\nbytes.insert(-100,4)\nbytes.insert(100,9)\nu8 b=bytes.pop(-2)\nbytes[-1]=8\nbool flags=[True,False]\nflags.append(True)\nbool f=flags.pop(0)\nint values=[1,2]\nvalues[0]+=10\nvalues[-1]-=3'),{ bytes: [4,1,255,8], b: 7, flags: [0,1], f: 1, values: [11,-1] })
})
test('Aliases share stable descriptors across resize; copy is independent and self-extend is safe',() => {
    assert.deepEqual(execute('int a=[1,2,3]\nint alias=a\nint copy=a.copy()\nint i=0\nwhile i<10:\n    a.append(i)\n    i+=1\ncopy[0]=99\ncopy.extend(copy)\ncopy.reverse()\nint first=alias[0]\nint size=len(alias)\nint repeats=copy.count(99)\nint position=copy.index(99)\na=a'),{ a: [1,2,3,0,1,2,3,4,5,6,7,8,9], alias: [1,2,3,0,1,2,3,4,5,6,7,8,9], copy: [3,2,99,3,2,99], i: 10, first: 1, size: 13, repeats: 2, position: 2 })
})
test('Reassignment releases old references without invalidating aliases, clear frees data',() => {
    assert.deepEqual(execute('int a=[1,2]\nint alias=a\na=[3]\nalias.append(4)\nint copy=a.copy()\na.clear()\na.append(7)\nint size=len(a)'),{ a: [7], alias: [1,2,4], copy: [3], size: 1 })
})
test('Local arrays are per invocation and allocator reuses memory across repeated calls',() => {
    assert.deepEqual(execute('def f(int n) -> int:\n    int a=[]\n    int alias=a\n    int i=0\n    while i<n:\n        a.append(i)\n        i+=1\n    return len(alias)\nint count=0\nint total=0\nwhile count<300:\n    total+=f(12)\n    count+=1'),{ count: 300, total: 3600 })
})
test('Recursive array locals survive nested calls and release on early returns',() => {
    assert.deepEqual(execute('def sum(int n) -> int:\n    int a=[n,n+1]\n    if n==0:\n        return a[0]\n    int nested=sum(n-1)\n    return a[0]+nested\nint result=sum(8)'),{ result: 36 })
})
test('Owned temporary arrays and copies are released after indexing, len, extend and ignored results',() => {
    assert.deepEqual(execute('int a=[]\nint i=0\nint total=0\nwhile i<120:\n    total+=len([1,2,3])\n    total+=[5,6][0]\n    a.extend([7,8])\n    a.copy()\n    a.copy()[0]=99\n    a.clear()\n    i+=1'),{ a: [], i: 120, total: 960 })
})
test('Array indices containing calls are evaluated once in augmented assignment',() => {
    assert.deepEqual(execute('int a=[10,20]\nint indexes=[0,1]\na[indexes.pop(0)]+=5\nint size=len(indexes)'),{ a: [15,20], indexes: [1], size: 1 })
})
test('Bounds, empty pop, missing value and exhausted heap fail explicitly',() => {
    for (const [source,message] of [
        ['int a=[1]\nint x=a[1]',/index out of range/],
        ['int a=[1]\na[-2]=3',/index out of range/],
        ['int a=[]\nint x=a.pop()',/empty array/],
        ['int a=[1]\na.remove(2)',/Value not found/],
        ['int a=[]\nwhile True:\n    a.append(1)',/out of memory/]
    ]) assert.throws(() => execute(source),message)
})

test('The exact get_sum array-parameter and for-in example returns 10',() => {
    assert.deepEqual(execute('def get_sum(int[] x) -> int:\n    int total = 0\n    for num in x:\n        total += num\n    return total\nint x = [1,2,3,4]\nint total = get_sum(x)'),{ x: [1,2,3,4], total: 10 })
})
test('Array parameters share mutations but rebinding does not replace the caller variable',() => {
    assert.deepEqual(execute('def mutate(int[] a) -> void:\n    a.append(3)\n    a[0]=7\n    a=[99]\n    a.append(100)\nint x=[1,2]\nmutate(x)\nint size=len(x)'),{ x: [7,2,3], size: 3 })
})
test('Array arguments support temporaries, recursive calls, aliasing and byte/bool elements',() => {
    assert.deepEqual(execute('def sum(int[] a,int n) -> int:\n    if n==0:\n        return 0\n    return a[n-1]+sum(a,n-1)\ndef bytes(u8[] a) -> int:\n    int total=0\n    for value in a:\n        total+=value\n    return total\ndef any(bool[] a) -> bool:\n    for value in a:\n        if value:\n            return True\n    return False\nint x=[1,2,3]\nint A=sum(x,3)\nint B=sum([4,5],2)\nint C=bytes([1,255])\nbool D=any([False,True])\nint size=len(x)'),{ x: [1,2,3], A: 6, B: 9, C: 256, D: 1, size: 3 })
})
test('Nested for loops handle break/continue, element copies, empty arrays and top-level iteration',() => {
    assert.deepEqual(execute('int a=[1,2,3]\nint total=0\nfor num in a:\n    if num==2:\n        continue\n    num+=10\n    total+=num\nfor value in [4,5]:\n    for inner in [1,2,3]:\n        if inner==2:\n            break\n        total+=value\nfor empty in []:\n    total=99'),{ a: [1,2,3], total: 33, num: 13, value: 5, inner: 2, empty: 0 })
})
test('For iteration retains its descriptor across rebinding and observes length changes',() => {
    assert.deepEqual(execute('def f() -> int:\n    int a=[1,2,3]\n    int total=0\n    for value in a:\n        total+=value\n        a=[99]\n    return total\ndef growing(int[] a) -> int:\n    int total=0\n    for value in a:\n        total+=value\n        if value<3:\n            a.append(value+1)\n    return total\nint A=f()\nint x=[1]\nint B=growing(x)'),{ A: 6, x: [1,2,3], B: 6 })
})
test('Repeated for early returns and borrowed/temporary parameters release references',() => {
    assert.deepEqual(execute('def first(int[] a) -> int:\n    for value in a:\n        return value\n    return 0\nint i=0\nint total=0\nwhile i<400:\n    total+=first([1,2,3])\n    i+=1'),{ i: 400, total: 400 })
})
