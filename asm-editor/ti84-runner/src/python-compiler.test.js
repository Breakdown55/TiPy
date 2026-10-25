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
    if (measurements?.seedGraph) runner.machine.memory.set(measurements.seedGraph,0x9340)
    const frames = []
    if (measurements) {
        const service = runner.serviceBcall.bind(runner)
        runner.lcd.dirty = false
        runner.serviceBcall = () => {
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
    const values = {}
    for (const [name,symbol] of Object.entries(result.symbols)) {
        const address = runner.assembly.asm.scopes[0].symbols.get(symbol.label).value
        let value = runner.machine.memory[address]
        if (symbol.type.endsWith('[]')) {
            const pointer = value | (runner.machine.memory[address + 1] << 8)
            const readWord = a => runner.machine.memory[a] | (runner.machine.memory[a + 1] << 8)
            const data = readWord(pointer)
            const length = readWord(pointer + 2)
            const width = readWord(pointer + 6)
            values[name] = Array.from({ length },(_,i) => {
                let element = width === 1 ? runner.machine.memory[data + i] : readWord(data + i * 2)
                if (symbol.type === 'i16[]' && element & 0x8000) element -= 65536
                return element
            })
            continue
        }
        if (symbol.size === 2) {
            value |= runner.machine.memory[address + 1] << 8
            if (value & 0x8000) value -= 65536
        }
        values[name] = value
    }
    return values
}

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
