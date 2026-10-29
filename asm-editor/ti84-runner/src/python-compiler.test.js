import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'
import { readFileSync } from 'node:fs'
import { Ti84Runner } from './hardware.js'

function execute(source, measurements) {
    const python = process.env.TIPY_PYTHON || 'python'
    const args = measurements?.project
        ? ['-c','import json,sys; from tipy.project import compile_project; from tipy.assembly_size import estimate; source=sys.stdin.read(); files=json.loads(source) if sys.argv[2]=="assets" else {"main.tip":source}; r=compile_project(files,"main.tip",optimization=sys.argv[1]); r["estimated_bytes"]=estimate(r["assembly"]); print(json.dumps(r))',measurements?.optimization ?? 'reference',measurements?.assets?'assets':'source']
        : measurements?.optimizeGraphics === false
        ? ['-c','import json,sys; from tipy import compile_source; print(json.dumps(compile_source(sys.stdin.read(), optimize_graphics=False, optimization="reference")))']
        : ['-m','tipy','-','--optimization', measurements?.optimization ?? 'reference','--show','all']
    if(measurements?.genericVertices)args[1]='import tipy.project as _p; _original=_p.generate; _p.generate=lambda tree,symbols: _original({**tree,"mesh_vertices_disabled":True},symbols); '+args[1]
    if(measurements?.noFrustum)args[1]='import tipy.project as _p; _original_frustum=_p.generate; _p.generate=lambda tree,symbols: _original_frustum({**tree,"mesh_frustum_disabled":True},symbols); '+args[1]
    if(measurements?.fullFont)args[1]='import tipy.text_runtime as _t; _full_text=_t.assembly; _t.assembly=lambda used,glyphs=None:_full_text(used,None); '+args[1]
    if(measurements?.opaqueReference)args[1]='from tipy import drawing_optimizer as _drawing; _drawing.annotate_blank_meshes=lambda tree: None; '+args[1]
    const processResult = spawnSync(python,args,{
        cwd: resolve('../..'), input: measurements?.assets?JSON.stringify({...measurements.assets,'main.tip':source}):source, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024
    })
    assert.ifError(processResult.error)
    assert.equal(processResult.status,0,processResult.stderr)
    const result = JSON.parse(processResult.stdout)
    const runner = new Ti84Runner(result.assembly)
    if(measurements?.initialCpuMode!==undefined)runner.writePort(0x20,measurements.initialCpuMode)
    const spanTable=runner.assembly.asm.scopes[0].symbols.get("__gfx_solid_fast_span_tables")
    if(spanTable)assert.equal(spanTable.value&255,0,"Span lookup tables must be page aligned")
    let minStack=65535
    if(measurements?.trackStack){
        const step=runner.machine.step.bind(runner.machine),symbols=runner.assembly.asm.scopes[0].symbols
        const clearStart=symbols.get('__gfx_clear_buffer_begin')?.value,clearEnd=symbols.get('__gfx_clear_buffer_end')?.value
        runner.machine.step=()=>{
            const {sp,pc}=runner.machine.z80.regs
            if(clearStart!==undefined&&pc>=clearStart&&pc<=clearEnd)assert.ok(sp>=0x9340&&sp<=0x9640,'Clear scratch stack stays inside graphics RAM')
            else if(sp)minStack=Math.min(minStack,sp)
            return step()
        }
    }
    const depth=runner.assembly.asm.scopes[0].symbols.get('__gfx_solid_depth')?.value
    const depthGuards=depth===undefined?null:[runner.machine.memory[depth-1],runner.machine.memory[depth+6144]]
    if(measurements?.guardGraph){runner.machine.memory[0x933f]=0xa5;runner.machine.memory[0x9640]=0x5a}
    for(const code of measurements?.heldKeys ?? [])runner.setKey(code,true)
    assert.ok(result.estimated_bytes>=runner.machineBytes,'RAM sizing must never underestimate assembled image bytes')
    if (measurements?.seedGraph) runner.machine.memory.set(measurements.seedGraph,0x9340)
    const frames = [],frameTimes=[]
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
            if (runner.lcd.dirty) {
                frames.push(runner.lcd.pixels.slice()); runner.lcd.dirty = false
                frameTimes.push(runner.machine.tStateCount)
                const nextKeys=measurements.afterFrameKeys?.[frames.length-1]
                if(nextKeys){runner.releaseKeys();for(const code of nextKeys)runner.setKey(code,true)}
                measurements.onFrame?.(runner,result,frames.length)
            }
        }
    }
    let keyEvent=0
    for (let i = 0; i < (measurements?.maxSlices ?? 400) && !runner.done; i++) {
        while(measurements?.keyTimeline?.[keyEvent]?.at<=runner.machine.tStateCount) {
            runner.releaseKeys();for(const code of measurements.keyTimeline[keyEvent++].keys)runner.setKey(code,true)
        }
        runner.runSlice()
    }
    assert.equal(runner.done,true,'Generated program must return')
    assert.equal(runner.machine.z80.regs.sp,0,'RET should pop the sentinel after a balanced expression stack')
    assert.equal(runner.machine.z80.regs.ix,0,'Functions must restore the caller frame pointer')
    assert.equal(runner.machine.z80.regs.iy,0x89f0,'Functions must preserve the calculator flags pointer')
    if(measurements?.guardGraph){assert.equal(runner.machine.memory[0x933f],0xa5);assert.equal(runner.machine.memory[0x9640],0x5a)}
    if(depthGuards)assert.deepEqual([runner.machine.memory[depth-1],runner.machine.memory[depth+6144]],depthGuards,'Depth writes must stay inside the buffer')
    if(measurements?.trackStack){assert.ok(minStack>=0x9d95+runner.machineBytes,'Native stack must stay above program code/data');measurements.stackBytes=65536-minStack}
    if (measurements) Object.assign(measurements, { bytes: runner.machineBytes, tStates: runner.machine.tStateCount,
        screen: runner.lcd.pixels.slice(), graph: runner.machine.memory.slice(0x9340,0x9640),
        report: result.optimization_report, assembly: result.assembly, frames,frameTimes,clockHz:runner.clockHz })
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
    for(const [name,fields] of Object.entries(result.record_symbols??{})){
        values[name]={}
        for(const [field,symbol] of Object.entries(fields)){
            const address=runner.assembly.asm.scopes[0].symbols.get(symbol.label)?.value
            if(address!==undefined)values[name][field]=decode(address,symbol.type)
        }
    }
    return values
}

test('Perspective camera changes XYZ drawings and leaves XY drawings in screen space',()=>{
    const m={project:true,maxSlices:20000}
    execute(`init_camera(fov=60,near=1,far=100)
clear()
point(10,0,0)
point(10,0,40)
point(0,10,0)
point(3,4)
render()
set_fov(120)
clear()
point(10,0,0)
point(3,4)
render()`,m)
    const expected={};execute('clear()\npixel_on(55,32)\npixel_on(62,32)\npixel_on(48,25)\npixel_on(3,4)\nrender()\nclear()\npixel_on(50,32)\npixel_on(3,4)\nrender()',expected)
    assert.deepEqual(m.frames,expected.frames)
    assert.doesNotMatch(m.assembly,/__obj_f32_multiply:/)
})

test('Perspective near/far and viewport clipping happen before narrowing coordinates',()=>{
    const m={project:true,maxSlices:20000}
    execute(`clear()
no_fill()
init_camera(60,10,100)
camera(0,0,0,0,0,-1)
line(-10,0,-5,10,0,-20)
render()
clear()
line(-20,0,-80,20,0,-120)
render()
clear()
line(-32768,-32768,-10,32767,32767,-10)
render()
clear()
line(-10,0,1,10,0,0)
point(0,0,-9)
point(0,0,-101)
render()`,m)
    const expected={};execute('clear()\nline(30,32,76,32)\nrender()\nclear()\nline(34,32,48,32)\nrender()\nclear()\nline(17,63,80,0)\nrender()\nclear()\nrender()',expected)
    assert.deepEqual(m.frames,expected.frames)
})
test('Runtime camera look-at agrees with constant setup for arbitrary positions and up vectors',()=>{
    for(const values of [[0,0,80,0,0,0,0,1,0],[80,0,0,0,0,0,0,1,0],[25,30,80,0,0,0,0,1,0],[0,80,0,0,0,0,0,0,-1],[0,0,0,1,1,0,0,0,1]]) {
        const a={project:true,maxSlices:20000},b={project:true,maxSlices:20000}
        const draw='clear()\npoint(10,0,0)\npoint(0,10,0)\npoint(0,0,10)\nline(-10,-10,0,10,10,0)\nrender()'
        execute('init_camera()\ncamera('+values.join(',')+')\n'+draw,a)
        const names=values.map((v,i)=>'v'+i)
        execute('init_camera()\n'+values.map((v,i)=>'int v'+i+'='+v).join('\n')+'\ncamera('+names.join(',')+')\n'+draw,b)
        assert.deepEqual(a.graph,b.graph,values.join(','))
    }
})
test('Perspective vertex paths fill clipped polygons and keep 2D paths separate',()=>{
    const m={project:true,maxSlices:20000}
    execute(`clear()
init_camera(60,10,100)
camera(0,0,0,0,0,-1)
fill(BLACK)
no_stroke()
begin_shape()
vertex(-10,-10,-20)
vertex(10,-10,-20)
vertex(0,10,-20)
end_shape(CLOSE)
render()
clear()
begin_shape()
vertex(-10,-10,-5)
vertex(10,-10,-20)
vertex(0,10,-20)
end_shape(CLOSE)
render()
clear()
begin_shape()
vertex(2,2)
vertex(12,2)
vertex(2,12)
end_shape(CLOSE)
render()`,m)
    const screenToGraph=screen=>Uint8Array.from({length:768},(_,i)=>Array.from({length:8},(_,b)=>screen[i*8+b]<<(7-b)).reduce((a,b)=>a|b,0))
    assert.deepEqual(screenToGraph(m.frames[0]),expectedPolygon([[20,60],[76,60],[48,4]],true))
    assert.deepEqual(screenToGraph(m.frames[1]),expectedPolygon([[11,50],[18,63],[70,63],[76,60],[48,4]],true))
    assert.deepEqual(screenToGraph(m.frames[2]),expectedPolygon([[2,2],[12,2],[2,12]],true))
})
test('Camera programs use a fixed-point model stack without changing 2D overlays',()=>{
    const m={project:true,maxSlices:20000}
    execute(`clear()
no_fill()
init_camera()
camera(0,0,80,0,0,0)
push_matrix()
rotate_y(1.5707963267948966)
point(0,0,10)
pop_matrix()
point(10,0,0)
point(3,4)
render()`,m)
    const e={};execute('clear()\npixel_on(55,32)\npixel_on(3,4)\nrender()',e)
    assert.deepEqual(m.graph,e.graph);assert.doesNotMatch(m.assembly,/__obj_f32_multiply:/)
})

test('Camera clipping retains thick strokes overlapping the screen edge',()=>{
    const m={project:true,maxSlices:20000},e={}
    execute('clear()\ninit_camera(90)\ncamera(0,0,0,0,0,-1)\nstroke_weight(5)\npoint(-49,0,-32)\npoint(48,0,-32)\nrender()',m)
    execute('clear()\nstroke_weight(5)\npoint(-1,32)\npoint(96,32)\nrender()',e)
    assert.deepEqual(m.graph,e.graph)
})

test('Camera matrix translation scaling and explicit matrices compose and restore',()=>{
    const m={project:true,maxSlices:20000},e={}
    execute(`clear()
init_camera(90)
push_matrix()
translate(10,20)
scale(2,3)
point(2,3)
pop_matrix()
apply_matrix(1,0,0,5,0,1,0,6,0,0,1,0,0,0,0,1)
point(2,3)
reset_matrix()
point(2,3)
render()`,m)
    execute('clear()\npixel_on(14,29)\npixel_on(7,9)\npixel_on(2,3)\nrender()',e)
    assert.deepEqual(m.graph,e.graph)
})

test('Constant camera rotations exactly match runtime coefficients on every axis',()=>{
    for(const axis of ['x','y','z'])for(const angle of ['0.5','-0.5','7']) {
        const a={project:true,maxSlices:20000},b={project:true,maxSlices:20000}
        const suffix='\npoint(12,5,10)\npoint(-8,-6,-5)\nrender()'
        execute('clear()\ninit_camera()\nrotate_'+axis+'('+angle+')'+suffix,a)
        execute('clear()\ninit_camera()\nangle='+angle+'\nrotate_'+axis+'(angle)'+suffix,b)
        assert.deepEqual(a.graph,b.graph,axis+': '+angle)
        assert.doesNotMatch(a.assembly,/__gfx_mat_rotate:/)
        assert.match(b.assembly,/__gfx_mat_rotate:/)
    }
})

test('Camera example keeps its rendered output with a smaller code and data budget',()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000}
    execute(readFileSync(resolve('../../examples/camera.tip'),'utf8'),m)
    const heap=Number(m.assembly.match(/__heap_start:\s*defs (\d+)/)[1])
    // Exact fast arithmetic and the bounded projection cache trade some of
    // the previous minimum-size budget for much cheaper animated frames.
    assert.ok(m.bytes-heap<=21400,'Camera demo must fit its speed-optimized code/data budget')
    assert.ok(heap>=2560,'The faster camera must retain useful allocation space')
    assert.equal(m.screen.reduce((sum,pixel)=>sum+pixel,0),180)
})

test('Native key_down polls held keys repeatedly and across simultaneous keypad groups',()=>{
    const m={heldKeys:[1,3,15,54],optimization:'reference'}
    const values=execute(`int hits=0
for i in range(20):
    if key_down("down") and key_down("right") and key_down("clear") and key_down("2nd"):
        hits+=1
bool left=key_down("left")
bool up=key_down("up")
int code=3
bool dynamic=key_down(code)
bool invalid=key_down(0) or key_down(-1) or key_down(57) or key_down(256)`,m)
    assert.equal(values.hits,20);assert.equal(values.left,0);assert.equal(values.up,0)
    assert.equal(values.dynamic,1);assert.equal(values.invalid,0)
    assert.doesNotMatch(m.assembly,/__heap_start:/)
})

test('Held arrows turn and pitch the room; releasing stops movement and Clear exits',()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000,heldKeys:[],
        afterFrameKeys:[[3],[3],[3,4],[15]]}
    execute(readFileSync(resolve('../../examples/room.tip'),'utf8'),m)
    assert.equal(m.frames.length,4)
    assert.notDeepEqual(m.frames[0],m.frames[1])
    assert.notDeepEqual(m.frames[1],m.frames[2])
    assert.notDeepEqual(m.frames[2],m.frames[3])
    assert.equal(m.clockHz,6_000_000,'Room restores the normal clock on exit')
    assert.ok(m.tStates<2_100_000,'Four room frames must stay within the shared-vertex pipeline budget')
    for(let i=1;i<m.frameTimes.length;i++)assert.ok(m.frameTimes[i]-m.frameTimes[i-1]<500_000,'Moving room frames must stay below 500,000 cycles')
})

test('Idle room polls promptly without continuously redrawing unchanged frames',()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000,
        keyTimeline:[{at:5_000_000,keys:[3]},{at:5_200_000,keys:[]},{at:15_000_000,keys:[15]}]}
    execute(readFileSync(resolve('../../examples/room.tip'),'utf8'),m)
    assert.equal(m.frames.length,2,'One initial frame and one response; idle frames are not redrawn')
    assert.notDeepEqual(m.frames[0],m.frames[1])
    assert.ok(m.frameTimes[1]-5_000_000<5_000_000,'First visible response must finish within 1/3 second at 15 MHz')
})

test('Native sleep retains real-time duration at the selected CPU speed',()=>{
    const slow={},fast={}
    execute('import time\nset_cpu_speed(6)\ntime.sleep(0.01)',slow)
    execute('import time\nset_cpu_speed(15)\ntime.sleep(0.01)\nset_cpu_speed(6)',fast)
    assert.ok(slow.tStates>55_000 && slow.tStates<65_000)
    assert.ok(fast.tStates>140_000 && fast.tStates<160_000)
    assert.equal(fast.clockHz,6_000_000)
})

test('Frame tick deltas track elapsed crystal time at either CPU speed',()=>{
    for(const speed of [6,15]){
        const result=execute(`import time\nset_cpu_speed(${speed})\nint previous=time.frame_ticks()\ntime.sleep(0.125)\nint elapsed=(time.frame_ticks()-previous)%256\ntime.stop_frame_timer()\nset_cpu_speed(6)`)
        assert.ok(result.elapsed>=15 && result.elapsed<=17,'128 Hz time must advance about 16 ticks in 125 ms')
    }
})

test('Fast camera multiplication and division remain exact across word boundaries',()=>{
    const p=spawnSync(process.env.TIPY_PYTHON || 'python',['-c',"from tipy.camera_math import routines,link; print(link(routines(),{'__gfx_u_mul32','__gfx_u_div32','__gfx_u_div64'}))"],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(p.status,0,p.stderr)
    const values=[0,1,255,256,32767,32768,65535,65536,65537,0x7fffffff,0x80000000,0xffffffff]
    let seed=123456789
    const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed}
    const pairs=values.map((a,i)=>[a,values[values.length-1-i]])
    pairs.push([65535,65535],[0xffffffff,65535],[65535,0xffffffff],[0xffffffff,0xffffffff])
    for(let i=0;i<48;i++)pairs.push([random(),i%3===0?random()&65535:random()])
    const instructions=[],data=[],checks=[]
    const words=(n,count)=>Array.from({length:count},(_,i)=>Number((BigInt(n)>>BigInt(i*16))&65535n)).join(',')
    for(const [i,[a,b]] of pairs.entries()) {
        const d=b || 1,n=(BigInt(a)<<32n)|BigInt(b)
        // Choose a denominator above the numerator's high word to keep the
        // exact 64/32 quotient within the documented unsigned 32-bit range.
        const divisor=a===0xffffffff?0xffffffff:Math.max(d,a+1)
        for(const [helper,args] of [
            ['u_mul32',[a&65535,a>>>16,b&65535,b>>>16,'p'+i]],
            ['u_div32',['n'+i,'d'+i,'q'+i,'r'+i]],
            ...(a===0xffffffff?[]:[['u_div64',['w'+i,'v'+i,'s'+i,'t'+i]]])]) {
            for(const arg of args)instructions.push('ld hl,'+arg,'push hl')
            instructions.push('call __gfx_'+helper,...args.map(()=>'pop bc'))
        }
        data.push('p'+i+': defs 8,0','n'+i+': dw '+words(a,2),'d'+i+': dw '+words(d,2),
            'q'+i+': defs 4,0','r'+i+': defs 4,0','w'+i+': dw '+words(n,4),'v'+i+': dw '+words(divisor,2),'s'+i+': defs 4,0','t'+i+': defs 4,0')
        checks.push(['p'+i,4,BigInt(a)*BigInt(b)],['q'+i,2,BigInt(a)/BigInt(d)],['r'+i,2,BigInt(a)%BigInt(d)])
        if(a!==0xffffffff)checks.push(['s'+i,2,n/BigInt(divisor)],['t'+i,2,n%BigInt(divisor)])
    }
    const runner=new Ti84Runner(['        org 0x9d95',...instructions.map(line=>'        '+line),'        ret',p.stdout,'__arr_fail:\n        halt',...data].join('\n'))
    for(let i=0;i<2000&&!runner.done;i++)runner.runSlice()
    assert.equal(runner.done,true);assert.equal(runner.machine.z80.regs.sp,0)
    for(const [label,count,expected] of checks){
        const address=runner.assembly.asm.scopes[0].symbols.get(label).value
        let value=0n;for(let j=0;j<count*2;j++)value|=BigInt(runner.machine.memory[address+j])<<BigInt(j*8)
        assert.equal(value,expected,label)
    }
})

test('Compact wireframe rasterizer matches the reference pixel walk in all octants',()=>{
    const p=spawnSync(process.env.TIPY_PYTHON || 'python',['-c',"from tipy.camera_math import link;from tipy.wireframe_runtime import _raster;print(link(_raster(),{'__gfx_wf_raster'}))"],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(p.status,0,p.stderr)
    const cases=[]
    for(const x of [0,12,47,80,95])for(const y of [0,15,32,63])cases.push([48,32,x,y],[x,y,48,32])
    cases.push([0,0,95,63],[95,63,0,0],[0,63,95,0],[1,1,1,1])
    for(const ink of [0,1])for(const values of cases){
        const instructions=values.flatMap(n=>['        ld hl,'+n,'        push hl'])
        const r=new Ti84Runner(['        org 0x9d95',...instructions,'        call __gfx_wf_raster',...values.map(()=>'        pop bc'),'        ret',p.stdout,'__gfx_ink: db '+ink].join('\n'))
        if(!ink)r.machine.memory.fill(255,0x9340,0x9640)
        for(let i=0;i<50&&!r.done;i++)r.runSlice()
        assert.equal(r.done,true);assert.equal(r.machine.z80.regs.sp,0);assert.equal(r.machine.z80.regs.ix,0);assert.equal(r.machine.z80.regs.iy,0x89f0)
        const expected=new Uint8Array(768);if(!ink)expected.fill(255)
        let [x,y,x2,y2]=values,dx=Math.abs(x2-x),dy=Math.abs(y2-y),sx=x<x2?1:-1,sy=y<y2?1:-1
        const steep=dy>dx;if(steep){[x,y]=[y,x];[x2,y2]=[y2,x2];[dx,dy]=[dy,dx];[sx,sy]=[sy,sx]}
        let error=dx>>1
        for(;;){const px=steep?y:x,py=steep?x:y,index=py*12+(px>>3),mask=128>>(px&7);if(ink)expected[index]|=mask;else expected[index]&=255^mask;if(x===x2)break;x+=sx;error+=dy;if(error>=dx){error-=dx;y+=sy}}
        assert.deepEqual(r.machine.memory.slice(0x9340,0x9640),expected,values.join(',')+': '+ink)
    }
})

test('Compact room projection remains within one LCD pixel of the reference renderer',()=>{
    const source=readFileSync(resolve('../../examples/room.tip'),'utf8')
    const lines=source.split('\n').filter(line=>line.trim().startsWith('line(')).map(line=>line.trim()).join('\n')
    function close(a,b){
        for(let y=0;y<64;y++)for(let x=0;x<96;x++)if(a[y*96+x]){
            let found=false
            for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++)if(x+dx>=0&&x+dx<96&&y+dy>=0&&y+dy<64&&b[(y+dy)*96+x+dx])found=true
            if(!found)return [x,y]
        }
        return null
    }
    for(const angle of [0,11.25,45,90,180,270])for(const pitch of [-700,-300,0,300,700]){
        const x=Math.round(Math.sin(angle*Math.PI/180)*1024),z=Math.round(-Math.cos(angle*Math.PI/180)*1024)
        const program=`no_fill()\ninit_camera(75,2,300)\ncamera(0,0,0,${x},${pitch},${z})\nclear()\n${lines}\nrender()`
        const fast={optimization:'aggressive'},reference={optimization:'reference'}
        execute(program,fast);execute(program,reference)
        assert.match(fast.assembly,/__gfx_cam_fast_batch:/)
        assert.equal(close(fast.screen,reference.screen),null,`fast -> ref ${angle}/${pitch}`)
        assert.equal(close(reference.screen,fast.screen),null,`ref -> fast ${angle}/${pitch}`)
    }
})

for(const gameFile of ['ion_dash','ion_dash_3d']) {
test(`${gameFile} starts, moves, dashes, collects a cell, and restores the CPU clock`,()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000,
        keyTimeline:[{at:1_000_000,keys:[9]},{at:2_000_000,keys:[3,54]},{at:6_000_000,keys:[15]}]}
    const values=execute(readFileSync(resolve(`../../examples/${gameFile}.tip`),'utf8'),m)
    assert.equal(values.mode,1);assert.ok(values.player_x>768)
    assert.ok(values.score>=1);assert.ok(values.dash_cooldown>0)
    assert.ok(m.frames.length>3);assert.ok(m.bytes<24_000)
    assert.equal(m.clockHz,6_000_000)
})

test(`${gameFile} reaches its victory and defeat screens without invalid cell indexing`,()=>{
    const source=readFileSync(resolve(`../../examples/${gameFile}.tip`),'utf8')
    for(const win of [true,false]) {
        let game=source.replace('int mode=0','int mode=1')
        game=win?game.replace('int score=0','int score=11'):
            game.replace('int lives=3','int lives=1').replace('int enemy_x=[240,','int enemy_x=[768,').replace('int enemy_y=[272,','int enemy_y=[496,')
        const m={project:true,optimization:'aggressive',maxSlices:20000,
            keyTimeline:[{at:3_000_000,keys:[15]}]}
        const values=execute(game,m)
        assert.equal(values.mode,win?4:2)
        assert.equal(win?values.score:values.lives,win?12:0)
        assert.ok(m.frames.length>=2)
    }
})

test(`${gameFile} pauses physics while continuing to poll the exit key`,()=>{
    const source=readFileSync(resolve(`../../examples/${gameFile}.tip`),'utf8').replace('int mode=0','int mode=1')
    const m={project:true,optimization:'aggressive',maxSlices:20000,
        keyTimeline:[{at:1_000_000,keys:[9]},{at:2_000_000,keys:[3]},{at:5_000_000,keys:[15]}]}
    const values=execute(source,m)
    assert.equal(values.mode,3);assert.equal(values.player_x,768)
    assert.ok(m.frameTimes.at(-1)<3_000_000)
})

}

const meshCube='int model=mesh([(-8,-8,-8),(8,-8,-8),(8,8,-8),(-8,8,-8),(-8,-8,8),(8,-8,8),(8,8,8),(-8,8,8)],[(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)])\n'
test('LUMEN RUN walks through all beacons, returns to the exit, wins and restarts',()=>{
    let won=false,restarted=false
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000,
        onFrame(r,result,count){
            assert.ok(count<180,'Game route should finish in a bounded number of frames')
            const value=name=>{const a=r.assembly.asm.scopes[0].symbols.get(result.symbols[name].label).value;const v=r.machine.memory[a]|r.machine.memory[a+1]<<8;return v>32767?v-65536:v}
            const stage=value('stage'),yaw=value('yaw')
            assert.ok(Math.abs(value('px'))<=224&&Math.abs(value('pz'))<=224)
            r.releaseKeys()
            if(stage===4){won=true;m.winFrame=r.lcd.pixels.slice();r.setKey(9,true)}
            else if(won){restarted=true;assert.equal(stage,0);assert.equal(value('px'),-160);assert.equal(value('pz'),160);r.setKey(15,true)}
            else {
                const [gx,gz]=[[-160,-160],[160,-160],[160,160],[-160,160]][stage]
                const aim=Math.round(Math.atan2(gx-value('px'),value('pz')-gz)*128/(2*Math.PI))&127
                const delta=((aim-yaw+64)&127)-64
                r.setKey(Math.abs(delta)<=4?4:delta>0?3:2,true)
            }
        }}
    execute(readFileSync(resolve('../../examples/lumen_run.tip'),'utf8'),m)
    assert.ok(won&&restarted);assert.ok(m.frames.length>30);assert.ok(m.bytes<24170)
    assert.ok(!m.assembly.includes('__gfx_solid_depth:'));assert.equal(m.clockHz,6_000_000)
})

test('LUMEN RUN clamps wall collisions and allows held-key strafing',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000,afterFrameKeys:Array.from({length:10},(_,i)=>i===9?[15]:[54,2])}
    const values=execute(readFileSync(resolve('../../examples/lumen_run.tip'),'utf8'),m)
    assert.equal(values.px,-224);assert.equal(values.pz,160);assert.equal(values.yaw,0)
})

test('LUMEN RUN held turning follows elapsed calculator time',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000,
        afterFrameKeys:Array.from({length:6},(_,i)=>i===5?[15]:[3]),
        onFrame(r,result){
            const a=r.assembly.asm.scopes[0].symbols.get(result.symbols.yaw.label).value
            ;(m.yaws??=[]).push(r.machine.memory[a]|r.machine.memory[a+1]<<8)
        }}
    const values=execute(readFileSync(resolve('../../examples/lumen_run.tip'),'utf8'),m)
    // First press immediately advances three phases; subsequent motion uses
    // the 128 Hz timer at one phase per three ticks, regardless of draw cost.
    const seconds=(m.frameTimes[4]-m.frameTimes[0])/15_000_000
    assert.ok(Math.abs(values.yaw-(3+seconds*128/3))<=2)
    assert.equal(m.yaws[1],3,'A newly held direction must respond immediately')
    const movingFrames=m.frameTimes.slice(1).map((t,i)=>t-m.frameTimes[i])
    assert.ok(Math.max(...movingFrames)<1_500_000,'Moving frames must remain below the measured 100 ms budget')
})

test('Single-pass mesh outlines match separate filled and wire draws',()=>{
    const model='int m=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2,12)])\n'
    for(const depth of ['True','False']){
        const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
        execute(model+`clear()\ndraw_mesh(m,cull=False,shaded=False,depth_test=${depth})\ndraw_mesh(m,filled=False,cull=False,depth_test=${depth})\nrender()\nclear()\ndraw_mesh(m,cull=False,shaded=False,depth_test=${depth},outline=True)\nrender()`,m)
        assert.deepEqual(m.frames[1],m.frames[0])
    }
})

test('Restoring mesh square root preserves exact bounded normalization',()=>{
    const vectors=[[1,0,0],[1,1,1],[-3,7,-11],[120,-120,13],[2047,2047,-2047],[0,0,-2047],[1023,2,-17],[17,29,-31]]
    const m={project:true,optimization:'aggressive',guardGraph:true,
        onFrame(r,result,count){
            const input=vectors[count-1],scaled=input.map(Math.abs)
            while(Math.max(...scaled)<1024)for(let i=0;i<3;i++)scaled[i]*=2
            const length=Math.floor(Math.sqrt(scaled.reduce((n,v)=>n+v*v,0)))
            const expected=scaled.map((v,i)=>Math.floor((v*256+(length>>1))/length)*(input[i]<0?-1:1))
            const base=r.assembly.asm.scopes[0].symbols.get('__gfx_light_records').value+2
            const actual=[0,2,4].map(o=>{const value=r.machine.memory[base+o]|r.machine.memory[base+o+1]<<8;return value>32767?value-65536:value})
            assert.deepEqual(actual,expected,`normalize ${input}`)
        }}
    execute(vectors.map(v=>`no_lights()\ndirectional_light(${v[0]}+int(key_down("left")),${v[1]},${v[2]})\nclear()\nrender()`).join('\n'),m)
    assert.equal(m.frames.length,vectors.length)
})

test('Aggressive lighting cache keeps separate instances distinct',()=>{
    const source='set_render_quality("aggressive")\nint m=mesh([(-5,-5,0),(5,-5,0),(0,5,0)],faces=[(0,1,2)])\ninit_camera(75,2,120)\n'
    const draw='clear()\nno_lights()\npoint_light(-8,0,12,radius=24,intensity=16)\ndraw_mesh(m,x=-8,cull=False,depth_test=False,outline=True)\ndraw_mesh(m,x=8,cull=False,depth_test=False,outline=True)\nrender()\n'
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
    execute(source+draw.repeat(5),m)
    for(const frame of m.frames)assert.deepEqual(frame,m.frames[0])
    assert.ok(m.assembly.includes('__gfx_quality_light_instance:'))
    assert.ok(m.stackBytes<1024)
})

test('Static pixel batches preserve arbitrary RAM and local loop results',()=>{
    const source='def paint(int flag) -> int:\n    int y=0\n    int x=0\n    if flag!=0:\n        pixel_on(0,0)\n    for y in range(-1,9):\n        for x in range(-2,97):\n            if (x+y)%3==0:\n                flip_pixel(x,y)\n            elif x%2==0:\n                pixel_off(x,y)\n            else:\n                pixel_on(x,y)\n    return y*100+x\nint final=paint(int(key_down("left")))\npixel_on(final%96,63)\nrender()'
    const seed=Uint8Array.from({length:768},(_,i)=>(i*57+139)&255)
    const baseline={project:true,optimization:'reference',seedGraph:seed,guardGraph:true,trackStack:true}
    const optimized={project:true,optimization:'aggressive',seedGraph:seed,guardGraph:true,trackStack:true}
    const before=execute(source,baseline),after=execute(source,optimized)
    assert.equal(before.final,896);assert.equal(after.final,896)
    assert.deepEqual(optimized.frames,baseline.frames)
    assert.ok(optimized.report.some(p=>p.pass==='static-pixel-batches'&&p.loops_lowered>0))
    assert.ok(optimized.tStates<baseline.tStates/3)
})

test('Merged coplanar faces preserve opaque pixels and explicit borders',()=>{
    const source='int m=mesh([(-18,-12,0),(18,-12,0),(18,12,0),(-18,12,0)],edges=[(0,1),(1,2),(2,3),(3,0)],faces=[(0,1,2,11),(0,2,3,11)])\nclear()\ndraw_mesh(m,cull=False,shaded=False,depth_test=False,outline=True)\nrender()'
    const baseline={project:true,optimization:'reference',guardGraph:true,trackStack:true}
    const optimized={project:true,optimization:'aggressive',guardGraph:true,trackStack:true}
    execute(source,baseline);execute('set_render_quality("aggressive")\n'+source,optimized)
    assert.deepEqual(optimized.frames,baseline.frames)
    assert.ok(optimized.tStates<baseline.tStates)
})

test('Convex depth-free scan conversion preserves clipped polygon coverage',()=>{
    const models=[
        [[-12,-10,0],[12,-10,0],[0,12,0]],
        [[-30,-25,0],[30,-25,0],[0,50,0]],
        [[-30,-5,8],[6,-25,-5],[12,35,5]],
        [[-20,-5,19],[6,-25,-5],[12,35,5]],
    ]
    for(const vertices of models){
        const model=`int m=mesh([${vertices.map(v=>`(${v.join(',')})`).join(',')}],faces=[(0,1,2,7)])\n`
        const images=[]
        for(const depth of ['True','False']){
            const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
            execute(model+`init_camera(75,2,120)\ncamera(0,0,20,0,0,0)\nclear()\ndraw_mesh(m,cull=False,shaded=False,depth_test=${depth})\nrender()`,m)
            images.push(m.frames[0])
        }
        const difference=images[0].reduce((n,p,i)=>n+(p!==images[1][i]),0)
        assert.ok(difference<=61,`Coverage differs by ${difference}/6144 pixels`)
    }
})

test('Two-chain rasterization handles both windings, horizontal edges and 3D clipping',()=>{
    const poses=[[0,0,0,0,0,20],[33,0,0,0,0,20],[0,35,0,0,0,20],
        [25,38,18,0,0,20],[0,0,64,0,0,20],[64,0,0,0,0,20],
        [27,19,12,0,0,5],[88,57,12,8,4,12]]
    for(const [i,[yaw,pitch,roll,ex,ey,ez]] of poses.entries()){
        const faces=i%2?'[(2,1,0,7),(3,2,0,7)]':'[(0,1,2,7),(0,2,3,7)]'
        const source=`set_render_quality("aggressive")\nint m=mesh([(-12,-10,0),(12,-10,0),(12,10,0),(-12,10,0)],faces=${faces})\ninit_camera(75,2,120)\ncamera(${ex},${ey},${ez},0,0,0)\n`
        const draw=depth=>`clear()\ndraw_mesh(m,yaw=${yaw},pitch=${pitch},roll=${roll},cull=False,shaded=False,depth_test=${depth})\nrender()\n`
        const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
        execute(source+draw('True')+draw('False'),m)
        const changed=m.frames[0].reduce((n,p,j)=>n+(p!==m.frames[1][j]),0)
        assert.ok(changed<=61,`${i}: coverage differs at ${changed}/6144 pixels`)
    }
})

test('Mixed depth-tested meshes and explicitly ordered overlays honor the per-draw switch',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000}
    execute('int a=mesh([(-12,-10,12),(12,-10,12),(0,12,12)],faces=[(0,1,2,0)])\nint b=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2,16)])\nclear()\ndraw_mesh(a,cull=False,shaded=False)\ndraw_mesh(b,cull=False,shaded=False)\nrender()\nclear()\ndraw_mesh(a,cull=False,shaded=False)\ndraw_mesh(b,cull=False,shaded=False,depth_test=False)\nrender()',m)
    assert.equal(m.frames[0][32*96+48],1);assert.equal(m.frames[1][32*96+48],0)
    assert.ok(m.frames[1].reduce((s,p)=>s+p,0)<m.frames[0].reduce((s,p)=>s+p,0))
    assert.ok(m.assembly.includes('__gfx_solid_depth:'));assert.ok(m.assembly.includes('__gfx_solid_flat_span:'))
})
test('Explicit directional and point lights add, attenuate and respect world-space normals',()=>{
    const model='int m=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2)])\n'
    const states=[
        'no_lights()',
        'no_lights()\npoint_light(0,0,24,radius=80)',
        'no_lights()\npoint_light(0,0,160,radius=80)',
        'no_lights()\npoint_light(0,0,-24,radius=80)',
        'no_lights()\ndirectional_light(0,0,1)',
        'no_lights()\ndirectional_light(0,0,1,intensity=4)\ndirectional_light(0,0,1,intensity=4)',
        'no_lights()\nambient_light(16)',
    ]
    const m={project:true,optimization:'aggressive',maxSlices:20000,guardGraph:true,trackStack:true}
    execute(model+states.map(s=>s+'\nclear()\ndraw_mesh(m,cull=False)\nrender()').join('\n'),m)
    const counts=m.frames.map(f=>f.reduce((s,p)=>s+p,0))
    assert.ok(counts[0]>0);assert.ok(counts[1]>0&&counts[1]<counts[0])
    assert.equal(counts[2],counts[0]);assert.equal(counts[3],counts[0]);assert.equal(counts[4],0)
    assert.ok(counts[5]>0&&counts[5]<counts[0]);assert.equal(counts[6],0)
    assert.ok(!m.assembly.includes('__gfx_wf_draw:'));assert.ok(!m.assembly.includes('__gfx_solid_facing:'))
})

test('Point light falloff follows object translation and rotation, and zero distance is defined',()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000,guardGraph:true}
    execute('int m=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2)])\nno_lights()\npoint_light(0,0,24,radius=80)\nclear()\ndraw_mesh(m,cull=False)\nrender()\nclear()\ndraw_mesh(m,x=20,cull=False)\nrender()\nclear()\ndraw_mesh(m,yaw=128,cull=False)\nrender()\nno_lights()\npoint_light(0,-3,0)\nclear()\ndraw_mesh(m,cull=False)\nrender()',m)
    const counts=m.frames.map(f=>f.reduce((s,p)=>s+p,0))
    assert.ok(counts[1]>counts[0]);assert.ok(counts[2]>counts[0]);assert.equal(counts[3],counts[2])
})

test('Four explicit lights saturate, and clearing them resets ambient and point-light state',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000}
    execute('int m=mesh([(-12,-10,0),(12,-10,0),(0,12,0)],faces=[(0,1,2)])\nno_lights()\n'+Array.from({length:4},()=> 'directional_light(0,0,1,intensity=8)').join('\n')+'\nclear()\ndraw_mesh(m,cull=False)\nrender()\nno_lights()\nclear()\ndraw_mesh(m,cull=False)\nrender()',m)
    assert.ok(!m.frames[0].some(Boolean));assert.ok(m.frames[1].some(Boolean))
    assert.throws(()=>execute('no_lights()\n'+Array.from({length:5},()=> 'point_light(0,0,1)').join('\n'),{project:true}),/Runtime error/)
})

test('Interactive lights demo moves a native point light and exits without stack or memory damage',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000,afterFrameKeys:[[3],[54],[15]]}
    execute(readFileSync(resolve('../../examples/lights.tip'),'utf8'),m)
    assert.equal(m.frames.length,3);assert.notDeepEqual(m.frames[0],m.frames[1]);assert.notDeepEqual(m.frames[1],m.frames[2])
    assert.equal(m.clockHz,6_000_000);assert.ok(m.bytes<24170)
})
test('Solid triangles cover pixel centers without cracks and implement all 17 Bayer shades',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
    const declarations=Array.from({length:17},(_,i)=>`int t${i}=mesh([(-16,-16,0),(16,-16,0),(0,16,0)],faces=[(0,1,2,${i})])`).join('\n')
    execute(declarations+'\nint plane_mesh=mesh([(-16,-16,0),(16,-16,0),(16,16,0),(-16,16,0)],faces=[(0,1,2,0),(0,2,3,0)])\ninit_camera(75,8,120)\n'+Array.from({length:17},(_,i)=>`clear()\ndraw_mesh(t${i},shaded=False)\nrender()`).join('\n')+'\nclear()\ndraw_mesh(plane_mesh,shaded=False)\nrender()',m)
    const bayer=[[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]]
    for(let level=0;level<17;level++){
        const expected=new Uint8Array(6144)
        for(let y=24;y<40;y++)for(let x=0;x<96;x++){
            const half=(y+0.5-24)/2
            if(x+0.5>=48-half && x+0.5<48+half && bayer[y&3][x&3]>=level)expected[y*96+x]=1
        }
        assert.deepEqual(m.frames[level],expected,`brightness ${level}`)
    }
    const quad=new Uint8Array(6144)
    for(let y=24;y<40;y++)for(let x=40;x<56;x++)quad[y*96+x]=1
    assert.deepEqual(m.frames[17],quad,'Shared triangle edge must leave no holes')
    assert.ok(m.bytes<24_170);assert.ok(m.stackBytes<1024)
})

test('Opaque white triangles occlude black ones in either submission order',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000}
    const source='int dark=mesh([(-16,-16,0),(16,-16,0),(0,16,0)],faces=[(0,1,2,0)])\nint white=mesh([(-16,-16,12),(16,-16,12),(0,16,12)],faces=[(0,1,2,16)])\ninit_camera(75,8,120)\n'
    execute(source+'clear()\ndraw_mesh(dark,shaded=False)\ndraw_mesh(white,shaded=False)\nrender()\nclear()\ndraw_mesh(white,shaded=False)\ndraw_mesh(dark,shaded=False)\nrender()',m)
    assert.ok(!m.frames[0].some(Boolean));assert.deepEqual(m.frames[0],m.frames[1])
})

test('Intersecting solid triangles use interpolated reciprocal depth rather than face sorting',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000}
    const source='int a=mesh([(-12,-12,24),(12,-12,-24),(0,12,0)],faces=[(0,1,2,0)])\nint b=mesh([(-12,-12,-24),(12,-12,24),(0,12,0)],faces=[(0,1,2,16)])\ninit_camera(75,8,120)\ncamera(0,0,0,0,0,-1)\n'
    execute(source+'clear()\ndraw_mesh(a,z=-48,cull=False,shaded=False)\ndraw_mesh(b,z=-48,cull=False,shaded=False)\nrender()\nclear()\ndraw_mesh(b,z=-48,cull=False,shaded=False)\ndraw_mesh(a,z=-48,cull=False,shaded=False)\nrender()',m)
    assert.deepEqual(m.frames[0],m.frames[1]);assert.ok(m.frames[0].some(Boolean))
    assert.equal(m.frames[0][36*96+42],1);assert.equal(m.frames[0][36*96+54],0)
})

test('Solid clipping crosses the near plane and LCD edges without writing outside buffers',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000}
    execute('int t=mesh([(-20,-8,0),(20,-8,-20),(0,20,12)],faces=[(0,1,2,0)])\ninit_camera(75,4,50)\ncamera(0,0,0,0,0,-1)\nclear()\ndraw_mesh(t,z=-8,cull=False,shaded=False)\nrender()',m)
    assert.ok(m.screen.some(Boolean));assert.ok(m.stackBytes<1024)
})

test('Backface winding, directional lighting, ambient light and wireframe mode execute natively',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,maxSlices:20000}
    execute('int t=mesh([(-16,-16,0),(16,-16,0),(0,16,0)],faces=[(0,2,1)])\ninit_camera(75,8,120)\nclear()\ndraw_mesh(t)\nrender()\nclear()\ndraw_mesh(t,cull=False,shaded=False)\nrender()\nclear()\nmesh_light(0,0,-1,ambient=0)\ndraw_mesh(t,cull=False)\nrender()\nclear()\nmesh_light(0,0,1,ambient=0)\ndraw_mesh(t,cull=False)\nrender()\nclear()\ndraw_mesh(t,filled=False)\nrender()',m)
    assert.ok(!m.frames[0].some(Boolean));assert.ok(!m.frames[1].some(Boolean))
    assert.ok(!m.frames[2].some(Boolean));assert.ok(m.frames[3].some(Boolean));assert.ok(m.frames[4].some(Boolean))
})

test('The OBJ chair demo fits calculator RAM, renders, handles input and exits cleanly',()=>{
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000,
        assets:{'chair.obj':readFileSync(resolve('../../examples/chair.obj'),'utf8')},afterFrameKeys:[[3],[54],[15]]}
    execute(readFileSync(resolve('../../examples/shaded_mesh.tip'),'utf8'),m)
    assert.ok(m.bytes<24_170);assert.ok(m.stackBytes<1024);assert.equal(m.clockHz,6_000_000)
    assert.ok(m.frames.length>=3);assert.notDeepEqual(m.frames[0],m.frames[1])
    assert.ok(m.frames[0].reduce((s,p)=>s+p,0)>100)
})
test('Mesh coefficient products match rounded signed Q8 arithmetic across signs and scales',()=>{
    const pairs=[]
    for(const a of [-15360,-257,-256,-171,-1,0,1,128,255,256,384,15360])
        for(const b of [-512,-256,-171,-1,0,1,128,255,256,384,512])pairs.push([a,b])
    const source=pairs.map(([a,b],i)=>`int v${i}=__tipy_native_mesh_mul(${a},${b})`).join('\n')
    const values=execute(source,{})
    for(const [i,[a,b]] of pairs.entries()) {
        const product=a*b,rounded=Math.floor(Math.abs(product)/256+0.5)
        assert.equal(values[`v${i}`],rounded===0?0:product<0?-rounded:rounded,`${a} * ${b}`)
    }
})

test('Unrolled unsigned kernels preserve full results, remainders and BC',()=>{
    const kernel=spawnSync(process.env.TIPY_PYTHON||'python',['-c','from tipy.wireframe_runtime import routines; print(next(s for s in routines(1) if s.startswith("__gfx_wf_umul:")))'],
        {cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(kernel.status,0,kernel.stderr)
    const divisions=[]
    for(const n of [0,1,127,128,255,256,257,32767,32768,65535,65536,1048575,16777215,4294901759])
        for(const d of [1,2,3,7,63,64,127,128,255,256,1024,65535])
            if(Math.floor(n/d)<=65535)divisions.push([n,d])
    for(const d of [127,128,255,256,257,1024,8191,32768,65535])for(const q of [0,1,15,255,256,511,512,4095,65535])divisions.push([q*d+(d>>1),d])
    const products=[[0,65535],[65535,0],[1,65535],[255,256],[256,255],[32768,32768],[65535,65535],[49153,20387]]
    for(const a of [1,2,15,16,255,256,257,3840,4095,4096,65535])for(const b of [1,127,128,255,256,257,32768,65535])products.push([a,b])
    const code=['org 0x9d95'],data=[]
    for(const [i,[n,d]] of divisions.entries()){
        code.push(`ld hl,${n&65535}`,`ld de,${Math.floor(n/65536)}`,`ld bc,${d}`,'call __gfx_wf_udiv',`ld (div_q_${i}),hl`,`ld (div_r_${i}),de`,`ld (div_bc_${i}),bc`)
        data.push(`div_q_${i}: dw 0`,`div_r_${i}: dw 0`,`div_bc_${i}: dw 0`)
    }
    for(const [i,[a,b]] of products.entries()){
        code.push(`ld de,${a}`,`ld bc,${b}`,'call __gfx_wf_umul',`ld (mul_lo_${i}),hl`,`ld (mul_hi_${i}),de`,`ld (mul_bc_${i}),bc`)
        data.push(`mul_lo_${i}: dw 0`,`mul_hi_${i}: dw 0`,`mul_bc_${i}: dw 0`)
    }
    const runner=new Ti84Runner([...code.map(line=>'        '+line),'        ret',...data,kernel.stdout].join('\n'))
    for(let i=0;i<400&&!runner.done;i++)runner.runSlice()
    assert.ok(runner.done)
    const word=name=>{const a=runner.assembly.asm.scopes[0].symbols.get(name).value;return runner.machine.memory[a]|runner.machine.memory[a+1]<<8}
    for(const [i,[n,d]] of divisions.entries()){
        assert.equal(word(`div_q_${i}`),Math.floor(n/d),`${n}/${d}`)
        assert.equal(word(`div_r_${i}`),n%d)
        assert.equal(word(`div_bc_${i}`),d)
    }
    for(const [i,[a,b]] of products.entries()){
        assert.equal(word(`mul_lo_${i}`),(a*b)%65536)
        assert.equal(word(`mul_hi_${i}`),Math.floor(a*b/65536))
        assert.equal(word(`mul_bc_${i}`),b)
    }
})

test('Indexed mesh pixels stay within one LCD pixel of the general camera renderer',()=>{
    const vertices=[[-8,-8,-8],[8,-8,-8],[8,8,-8],[-8,8,-8],[-8,-8,8],[8,-8,8],[8,8,8],[-8,8,8]]
    const edges=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]]
    const close=(a,b)=>{
        for(let y=0;y<64;y++)for(let x=0;x<96;x++)if(a[y*96+x]) {
            let found=false
            for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++)if(x+dx>=0&&x+dx<96&&y+dy>=0&&y+dy<64&&b[(y+dy)*96+x+dx])found=true
            if(!found)return false
        }
        return true
    }
    for(const [yaw,pitch] of [[0,0],[32,20],[64,32],[170,210]]) {
        const m={},r={}
        execute(meshCube+`init_camera(75,2,120)\nclear()\ndraw_mesh(model,yaw=${yaw},pitch=${pitch},size=384)\nrender()`,m)
        execute('no_fill()\ninit_camera(75,2,120)\ncamera(0,0,80,0,0,0)\nclear()\n'+`rotate_y(${yaw*Math.PI/128})\nrotate_x(${pitch*Math.PI/128})\nscale(1.5)\n`+edges.map(([a,b])=>`line(${[...vertices[a],...vertices[b]].join(',')})`).join('\n')+'\nrender()',r)
        assert.ok(m.screen.some(Boolean));assert.ok(r.screen.some(Boolean))
        assert.ok(close(m.screen,r.screen)&&close(r.screen,m.screen),`pose ${yaw}/${pitch}`)
    }
})

test('Indexed meshes rotate, scale, translate, change camera and FOV without heap allocation',()=>{
    const m={project:true,optimization:'aggressive'}
    execute(meshCube+'init_camera(fov=75,near=2,far=120)\nclear()\ndraw_mesh(model)\nrender()\nclear()\ndraw_mesh(model,yaw=32,pitch=20,roll=12)\nrender()\nclear()\ndraw_mesh(model,x=15,size=384)\nrender()\nclear()\ncamera(20,10,70,0,0,0)\nset_fov(60)\ndraw_mesh(model)\nrender()',m)
    assert.equal(m.frames.length,4)
    for(let i=1;i<4;i++)assert.notDeepEqual(m.frames[i],m.frames[0])
    for(const frame of m.frames)assert.ok(frame.reduce((n,p)=>n+p,0)>20)
    assert.ok(m.bytes<13_000,`mesh image ${m.bytes}`);assert.ok(!m.assembly.includes('__heap_start:'))
    assert.ok(!m.assembly.includes('__gfx_cam_project:'))
})

test('Indexed mesh repeated draws stay below a native CPU cycle budget',()=>{
    const m={project:true,optimization:'aggressive'}
    execute(meshCube+'init_camera()\nclear()\ndraw_mesh(model,yaw=32,pitch=20)\nrender()\nclear()\ndraw_mesh(model,yaw=33,pitch=20)\nrender()',m)
    const cycles=m.frameTimes[1]-m.frameTimes[0]
    assert.ok(cycles<450_000,`steady mesh frame uses ${cycles} T-states`)
    assert.ok(!m.assembly.includes('__heap_start:'))
})

test('Indexed mesh depth and viewport clipping preserve the graph buffer boundaries',()=>{
    const source='int model=mesh([(-20,-12,-12),(20,-12,-12),(0,20,12)],[(0,1),(1,2),(2,0)])\n'
    const m={project:true,optimization:'aggressive',guardGraph:true}
    execute(source+'init_camera(75,5,100)\ncamera(0,0,0,0,0,-1)\nclear()\ndraw_mesh(model,z=-8)\nrender()\nclear()\ndraw_mesh(model,z=30)\nrender()\nclear()\ndraw_mesh(model,z=-115)\nrender()\nclear()\ndraw_mesh(model,z=90)\nrender()',m)
    assert.ok(m.frames[0].some(Boolean));assert.ok(!m.frames[1].some(Boolean))
    assert.ok(!m.frames[2].some(Boolean))
    assert.ok(!m.frames[3].some(Boolean))
    assert.ok(m.frameTimes[3]-m.frameTimes[2]<150_000,'bounding volume should reject the distant model before transforming vertices')
})

test('ION DASH 3D projects depth, height, and shared actor vertices on the calculator',()=>{
    const source=readFileSync(resolve('../../examples/ion_dash_3d.tip'),'utf8')
    const functions=source.slice(source.indexOf('def project_x('),source.indexOf('def arena('))
    const m={project:true,optimization:'reference'}
    const values=execute(functions+'\nint center=project_x(0,0,0)\nint near_x=project_x(30,0,30)\nint far_x=project_x(30,0,-30)\nint floor_y=project_y(0,0)\nint raised_y=project_y(10,0)\nint near_y=project_y(0,30)\nint far_y=project_y(0,-30)\nclear()\nactor(768,496,10,True)\nrender()',m)
    assert.equal(values.center,48);assert.ok(values.near_x>values.far_x)
    assert.ok(values.raised_y<values.floor_y);assert.ok(values.near_y>values.far_y)
    assert.ok(m.screen.reduce((sum,pixel)=>sum+pixel,0)>10)
})

test('MONO/96 cycles through three animated native 2D effects and exits cleanly',()=>{
    const m={project:true,optimization:'aggressive',maxSlices:20000,afterFrameKeys:[[3],[],[3],[],[2],[],[15]]}
    execute(readFileSync(resolve('../../examples/mono96.tip'),'utf8'),m)
    assert.equal(m.frames.length,7);assert.equal(m.clockHz,6_000_000)
    assert.notDeepEqual(m.frames[0],m.frames[1]);assert.notDeepEqual(m.frames[1],m.frames[3])
    assert.ok(m.bytes<24_000);assert.ok(m.tStates<10_000_000)
    for(const frame of m.frames){
        assert.ok(frame.reduce((sum,pixel)=>sum+pixel,0)>700)
        for(let x=0;x<96;x++){assert.equal(frame[x],1);assert.equal(frame[63*96+x],1)}
        for(let y=0;y<64;y++){assert.equal(frame[y*96],1);assert.equal(frame[y*96+95],1)}
    }
})

test('MONO/96 freezes without repeatedly redrawing and still polls Clear',()=>{
    const source=readFileSync(resolve('../../examples/mono96.tip'),'utf8').replace('int scene=0','int scene=1')
    const m={project:true,optimization:'aggressive',maxSlices:20000,
        keyTimeline:[{at:2_000_000,keys:[9]},{at:3_000_000,keys:[]},{at:7_000_000,keys:[15]}]}
    execute(source,m)
    assert.equal(m.frames.length,3)
    assert.ok(m.frameTimes[2]<5_000_000);assert.ok(m.tStates>=7_000_000)
})

test('Byte spans preserve masked neighbors, white fills, clipping, and raw black axis lines',()=>{
    const m={}
    execute('background(BLACK)\nno_stroke()\nfill(WHITE)\nrect(-3,2,14,8)\nrect(13,12,70,4)\nrect(90,50,10,20)\nh_line(0,4,95,4)\nv_line(8,0,8,63)\nrender()',m)
    const expected=new Uint8Array(768);expected.fill(255)
    for(const [x1,x2,y1,y2] of [[0,10,2,9],[13,82,12,15],[90,95,50,63]])
        for(let y=y1;y<=y2;y++)for(let x=x1;x<=x2;x++)expected[y*12+(x>>3)]&=255^(128>>(x&7))
    for(let x=0;x<96;x++)expected[4*12+(x>>3)]|=128>>(x&7)
    for(let y=0;y<64;y++)expected[y*12+1]|=128
    assert.deepEqual(m.graph,expected)
})

function expectedCurve(cx,cy,rx,ry,filled) {
    const inside=(x,y)=>{
        if(rx<0||ry<0)return false
        const dx=BigInt(x-cx),dy=BigInt(y-cy),a=BigInt(rx),b=BigInt(ry)
        if(!rx)return x===cx&&Math.abs(y-cy)<=ry
        if(!ry)return y===cy&&Math.abs(x-cx)<=rx
        return dx*dx*b*b+dy*dy*a*a<=a*a*b*b
    }
    return Uint8Array.from({length:768},(_,index)=>{
        const y=Math.floor(index/12),x=(index%12)*8
        let value=0
        for(let bit=0;bit<8;bit++)if(inside(x+bit,y)&&(filled||[[1,0],[-1,0],[0,1],[0,-1]].some(([a,b])=>!inside(x+bit+a,y+b))))value|=128>>bit
        return value
    })
}
test('New native circles and ellipses exactly rasterize filled and outline shapes with clipping',()=>{
    for(const [cx,cy,rx,ry] of [[25,20,8,8],[0,0,9,5],[94,62,13,4],[30,30,0,8],[30,30,8,0],[10,10,0,0],[48,32,-1,2],[32767,20,32767,12],[-32768,0,32767,32767],[48,32,32767,32767]])for(const filled of [false,true]) {
        const m={maxSlices:20000}
        execute(`clear()\n__tipy_native_ellipse(${cx},${cy},${rx},${ry},${filled?'True':'False'})\nrender()`,m)
        assert.deepEqual(m.graph,expectedCurve(cx,cy,rx,ry,filled),`ellipse ${[cx,cy,rx,ry,filled]}`)
        if(rx===ry&&rx>=0) {
            const circle={maxSlices:20000};execute(`clear()\n__tipy_native_circle(${cx},${cy},${rx},${filled?'True':'False'})\nrender()`,circle)
            assert.deepEqual(circle.graph,m.graph)
            assert.doesNotMatch(circle.assembly,/__heap_start:|__gfx_mul32:|__gfx_ellipse:/)
        }
    }
})
function expectedPolygon(points,filled,closed=true) {
    const pixels=new Uint8Array(6144)
    const on=(x,y)=>{if(x>=0&&x<96&&y>=0&&y<64)pixels[y*96+x]=1}
    if(filled&&points.length>=3)for(let y=0;y<64;y++)for(let x=0;x<96;x++) {
        let inside=false
        for(let i=0;i<points.length;i++) {
            let [a,b]=points[i],[c,d]=points[(i+1)%points.length]
            if(b>d){[a,c]=[c,a];[b,d]=[d,b]}
            if(y>=b&&y<d&&(x-a)*(d-b)<(c-a)*(y-b))inside=!inside
        }
        if(inside)on(x,y)
    }
    const edge=([a,b],[c,d])=>{
        const swap=Math.abs(d-b)>Math.abs(c-a)
        if(swap){[a,b]=[b,a];[c,d]=[d,c]}
        const dx=Math.abs(c-a),dy=Math.abs(d-b),sx=a<c?1:-1,sy=b<d?1:-1
        for(let i=0;i<=dx;i++) {
            const major=a+sx*i,minor=b+sy*(dx?Math.floor((Math.floor(dx/2)+i*dy)/dx):0)
            if(swap)on(minor,major);else on(major,minor)
        }
    }
    if(points.length===1)on(...points[0])
    for(let i=1;i<points.length;i++)edge(points[i-1],points[i])
    if(closed&&points.length>1)edge(points.at(-1),points[0])
    return Uint8Array.from({length:768},(_,i)=>Array.from({length:8},(_,b)=>pixels[i*8+b]<<(7-b)).reduce((a,b)=>a|b,0))
}
test('Native polygon scanlines fill concave shapes and preserve exact i16 edge intersections',()=>{
    for(const points of [[],[[4,5]],[[2,2],[20,20]],[[10,10],[40,10],[20,40]],[[4,4],[40,4],[40,12],[12,12],[12,40],[4,40]],[[5,5],[50,45],[5,45],[50,5]],[[-32768,-32768],[32767,32767],[32767,-32768]],[[-32768,10],[32767,11],[30,50]]])for(const filled of [false,true]) {
        const source='['+points.map(p=>'('+p.join(',')+')').join(',')+']',m={maxSlices:20000}
        execute(`clear()\n__tipy_native_polygon(${source},${filled?'True':'False'})\nrender()`,m)
        assert.deepEqual(m.graph,expectedPolygon(points,filled),`polygon ${JSON.stringify(points)}, ${filled}`)
        if(points.length===3) {
            const triangle={maxSlices:20000};execute(`clear()\n__tipy_native_triangle(${points.flat().join(',')},${filled?'True':'False'})\nrender()`,triangle)
            assert.deepEqual(triangle.graph,m.graph)
        }
    }
    const points=[[1,1],[30,40],[80,2]],source='[(1,1),(30,40),(80,2)]'
    for(const closed of [false,true]) {
        const m={};execute(`clear()\n__tipy_native_polyline(${source},${closed?'True':'False'})\nrender()`,m)
        assert.deepEqual(m.graph,expectedPolygon(points,false,closed))
        assert.doesNotMatch(m.assembly,/__gfx_intersection:/)
    }
})
test('Py5 matrix stack composes transforms and restores only the coordinate system',()=>{
    const m={maxSlices:20000,project:true}
    execute(`clear()
no_fill()
point(1,2)
push_matrix()
translate(40,20)
scale(2,3)
point(1,2)
push_matrix()
translate(1,1)
point(0,0)
pop_matrix()
point(2,0)
pop_matrix()
point(3,4)
render()`,m)
    const expected={};execute('clear()\n' + [[1,2],[42,26],[42,23],[44,20],[3,4]].map(([x,y])=>`pixel_on(${x},${y})`).join('\n')+'\nrender()',expected)
    assert.deepEqual(m.graph,expected.graph)
})
test('Py5 axis rotations accept radians and project 3D vertices orthographically',()=>{
    const m={maxSlices:20000,project:true}
    execute(`clear()
push_matrix()
translate(48,32,0)
rotate_x(1.5707963267948966)
point(0,0,10)
pop_matrix()
push_matrix()
translate(48,32,0)
rotate_y(1.5707963267948966)
point(0,0,10)
pop_matrix()
push_matrix()
translate(48,32)
rotate(1.5707963267948966)
point(10,0)
pop_matrix()
reset_matrix()
point(0,0)
render()`,m)
    const expected={};execute('clear()\npixel_on(48,22)\npixel_on(58,32)\npixel_on(48,42)\npixel_on(0,0)\nrender()',expected)
    assert.deepEqual(m.graph,expected.graph)
    assert.doesNotMatch(m.assembly,/__native_sin_table:/)
})

test('Py5 fill, erasing, stroke widths and custom vertex paths preserve monochrome styles',()=>{
    const m={project:true,maxSlices:20000}
    execute(`clear()
fill(BLACK)
no_stroke()
rect(2,2,30,30)
fill(WHITE)
circle(16,16,12)
no_fill()
stroke(BLACK)
stroke_weight(2)
rect(40,4,20,20)
stroke_weight(1)
begin_shape()
vertex(65,5)
vertex(85,5)
vertex(75,25)
end_shape(CLOSE)
render()`,m)
    const expected={};execute('clear()\nrectangle(2,31,2,31,1,True)\nrectangle(40,59,4,23,2,False)\nline(65,5,85,5)\nline(85,5,75,25)\nline(75,25,65,5)\nrender()',expected)
    const erased=expectedCurve(16,16,6,6,true)
    assert.deepEqual(m.graph,expected.graph.map((v,i)=>v&~erased[i]))
    assert.doesNotMatch(m.assembly,/__gfx_sin_quarter:|__native_sin_table:/)
})
test('Py5 namespace aliases and background colors compile without a host library',()=>{
    const m={project:true};execute('import py5 as p\nfrom py5 import WHITE as white\np.background(p.BLACK)\np.no_stroke()\np.fill(white)\np.rect(2,3,10,8)\nrender()',m)
    const expected=new Uint8Array(768).fill(255)
    for(let y=3;y<11;y++)for(let x=2;x<12;x++)expected[y*12+(x>>3)]&=~(128>>(x&7))
    assert.deepEqual(m.graph,expected)
})
test('Py5 helper pruning preserves drawing and transforms in definition-time defaults',()=>{
    const source='def effect():\n    circle(10,10,4)\n    return 0\ndef unused(int value=effect()):\n    pass\nno_fill()\npoint(0,0)\nrender()'
    const expected={seedGraph:new Uint8Array(768).fill(255)}
    execute('__tipy_native_ink(0)\n__tipy_native_circle(10,10,2,True)\n__tipy_native_ink(1)\n__tipy_native_circle(10,10,2,False)\npixel_on(0,0)\nrender()',expected)
    for(const optimization of ['reference','aggressive']) {
        const m={optimization,project:true,seedGraph:new Uint8Array(768).fill(255)};execute(source,m);assert.deepEqual(m.graph,expected.graph)
        const transformed={optimization,project:true}
        execute('def move():\n    translate(20,10)\n    return 0\ndef unused(int value=move()):\n    pass\nclear()\npoint(0,0)\nrender()',transformed)
        assert.deepEqual(transformed.graph,Uint8Array.from({length:768},(_,i)=>i===10*12+2?8:0))
    }
})
test('Py5 transforms preserve caller vertices and reclaim repeated temporary shapes',()=>{
    const m={project:true,maxSlices:20000}
    const v=execute(`clear()
no_fill()
points=[(0,0),(8,0),(4,8)]
for frame in range(30):
    push_matrix()
    translate(20,10)
    scale(2,1)
    polygon(points)
    pop_matrix()
render()`,m)
    assert.deepEqual(v.points,[[0,0],[8,0],[4,8]])
    assert.deepEqual(m.graph,expectedPolygon([[20,10],[36,10],[28,18]],false))
})
for(const name of ['graphics_2d.tip','graphics_3d_ready.tip'])test(`Py5 example ${name} runs through browser compilation`,()=>{
    const source=readFileSync(resolve('../../examples/'+name),'utf8')
    for(const optimization of ['reference','aggressive']) {
        const m={project:true,optimization,maxSlices:20000};execute(source,m)
        assert.ok(m.graph.some(v=>v));assert.ok(m.frames.length===1)
        assert.ok(m.bytes<0xfffe-0x9d95)
    }
})

const featureDemo = readFileSync(resolve('../../examples/feature_test.tip'),'utf8')
const featureTitles = [...featureDemo.matchAll(/begin\("([^"]+)"\)/g)].map(match=>match[1])
for(let page=1;page<=24;page++)test(`Feature demo page ${page} displays six native passes`,t=>{
    const title=featureTitles[page-1]
    const expected={}
    execute(`clear()\nprint(${JSON.stringify(title)},0,0)\n`+Array.from({length:6},(_,i)=>`print("${i+1}: OK",0,${(i+1)*8})`).join('\n')+'\nprint("6/6 PASS",0,56)\nrender()',expected)
    for(const optimization of ['reference','aggressive']) {
        const observed={maxSlices:20000,optimization,project:true}
        execute(featureDemo.replace(/test_01\(\)\s*$/,`test_${String(page).padStart(2,'0')}()`),observed)
        for(let row=0;row<8;row++)assert.deepEqual(observed.graph.slice(row*96,(row+1)*96),expected.graph.slice(row*96,(row+1)*96),`Page ${page}, ${optimization}, row ${row}: expected ${row===0?title:row===7?'6/6 PASS':row+': OK'}`)
        t.diagnostic(`${title}, ${optimization}: ${observed.bytes} bytes`)
    }
})
test('Feature demo graphics page renders clipping, transparent text and all primitives',()=>{
    const observed={project:true}
    execute(featureDemo.replace(/test_01\(\)\s*$/,'test_25()'),observed)
    const pixel=(x,y)=>!!(observed.graph[y*12+(x>>3)]&(128>>(x&7)))
    for(let x=0;x<96;x++)assert.ok(pixel(x,0)&&pixel(x,63))
    for(let y=0;y<64;y++)assert.ok(pixel(0,y)&&pixel(95,y))
    assert.ok(pixel(44,16));assert.ok(!pixel(45,16));assert.ok(!pixel(46,16))
    for(let y=20;y<=40;y++)for(let x=65;x<=85;x++)assert.ok(pixel(x,y))
    assert.ok(!pixel(94,5));assert.ok(!pixel(1,5))
})
test('Feature demo input page accepts 42 and ABC through native keyboard polling',()=>{
    const observed={pollKeys:[...Array(55).fill(0),35,0,26,0,9,0,48,0,47,0,39,0,31,0,9],maxSlices:20000,project:true}
    execute(featureDemo.replace(/test_01\(\)\s*$/,'test_26()'),observed)
    const expected={}
    execute('clear()\nrectangle(0,95,0,63,1,False)\nprint("26 INPUT",3,3)\npixel_on(80,20)\nprint("N: 42",3,16)\nprint("S: ABC",3,32)\nprint("INPUT PASS",3,50)\nrender()',expected)
    assert.deepEqual(observed.graph,expected.graph)
    assert.ok(observed.frames.length>4,'Native input should render intermediate cursor/answer frames')
})

test('Expanded language features preserve rendered output across optimization modes', () => {
    const source=readFileSync(resolve('../../examples/closures_and_containers.tip'),'utf8')
    const results=['reference','balanced','aggressive'].map(optimization=>{
        const measurements={optimization,maxSlices:12000};execute(source,measurements);return measurements
    })
    for(const result of results.slice(1))assert.deepEqual(result.graph,results[0].graph)
})

test('Dictionary views compare by keys/items and values views preserve identity', () => {
    const v=execute(`a={"x":[1],"y":[2]}
b={"y":[20],"x":[10]}
bool keys=a.keys()==b.keys()
bool keys_set=a.keys()=={"x","y"}
bool subset={"x"}<a.keys()
bool items=a.items()==b.items()
copy={"y":[2],"x":[1]}
bool same_items=a.items()==copy.items()
values=a.values()
alias=values
bool same=values==alias
bool different=values!=a.values()
bool nested=[a.keys()]==[b.keys()]
bool nested_values=[a.values()]==[b.values()]
`,{maxSlices:12000})
    assert.deepEqual([v.keys,v.keys_set,v.subset,v.items,v.same_items,v.same,v.different,v.nested,v.nested_values],[1,1,1,0,1,1,1,1,0])
})

test('Native class identity remains distinct from tuple equality and cycles are collected', () => {
    const v=execute(`class Node:
    def __init__(self,int value):
        self.value=value
        self.next=self
kept=Node(99)
temporary=Node(0)
for iteration in range(300):
    temporary=Node(iteration)
bool distinct=[kept]==[Node(99)]
bool shared=[kept]==[kept]
last=temporary.value
alive=kept.next.value
`,{maxSlices:20000})
    assert.equal(v.distinct,0);assert.equal(v.shared,1);assert.equal(v.last,299);assert.equal(v.alive,99)
})

test('Managed operands and print text survive reentrant argument evaluation', () => {
    const v=execute(`def run():
    text="Old"
    numbers=[1,2]
    key="before"
    table={"existing":0}
    def change():
        nonlocal text
        text="New"
        return "!"
    def index():
        nonlocal numbers
        numbers=[3,4]
        return 1
    def right():
        nonlocal key
        key="after"
        return 7
    joined=text+change()
    value=numbers[index()]
    table[key]=right()
    return joined,value,table
joined,value,table=run()
clear()
def coordinate():
    print(99,40,10)
    return 0
print(12,coordinate(),0)
render()
`,{maxSlices:12000})
    assert.equal(v.joined,'Old!');assert.equal(v.value,2);assert.deepEqual(v.table,new Map([['existing',0],['before',7]]))
    const observed={},expected={}
    execute('clear()\ndef coordinate():\n    print(99,40,10)\n    return 0\nprint(12,coordinate(),0)\nrender()',observed)
    execute('clear()\nprint(99,40,10)\nprint(12,0,0)\nrender()',expected)
    assert.deepEqual(observed.graph,expected.graph)
})

test('Streaming aggregates and typed truth preserve short-circuit iterator consumption', () => {
    const v=execute(`events=[]
def values():
    for n in range(5):
        events.append(n)
        yield n
iterator=values()
bool found=any(iterator)
next_value=next(iterator)
bool remaining=all(iterator)
total=sum(values())
smallest=min(["z","a","b"])
largest=max(iter(["z","a","b"]))
kept=list(filter(None,["","A","","B"]))
bool empty=bool([])
bool zero=bool(i32(0))
bool nonzero=bool(float(-0.5))
bool negative_zero=bool(float(-0.0))
optional=None
optional=0
bool optional_zero=bool(optional)
bool missing=optional==None
bool integer_none=0==None
class Empty:
    pass
obj=Empty()
bool object_truth=bool(obj)
`,{maxSlices:18000})
    assert.deepEqual([v.found,v.next_value,v.remaining,v.total],[1,2,1,10]);assert.equal(v.smallest,'a');assert.equal(v.largest,'z');assert.deepEqual(v.kept,['A','B'])
    assert.deepEqual([v.empty,v.zero,v.nonzero,v.negative_zero,v.optional_zero,v.missing,v.integer_none,v.object_truth],[0,0,1,0,0,0,0,1])
    assert.deepEqual(v.events,[0,1,2,3,4,0,1,2,3,4])
    const numbers=execute('wide=sum([i32(100000),i32(200000)])\nfloat_total=sum([float(1.25),float(2.5)])',{maxSlices:10000})
    assert.equal(numbers.wide,300000);assert.equal(numbers.float_total,3.75)
})

test('Recursive closures reclaim captured cycles and later bindings remain shared', () => {
    const v=execute(`def factory():
    def factorial(int n) -> int:
        if n<2:
            return 1
        return n*factorial(n-1)
    return factorial
factorial=factory()
answer=factorial(6)
for iteration in range(120):
    factorial=factory()
last=factorial(5)
def forward():
    def read():
        return text
    text="later"
    return read
reader=forward()
text=reader()
def invalid():
    def read() -> int:
        return value
    read()
    value=7
    return value
caught=False
try:
    invalid()
except UnboundLocalError:
    caught=True
`,{maxSlices:16000})
    assert.equal(v.answer,720);assert.equal(v.last,120);assert.equal(v.text,'later');assert.equal(v.caught,1)
})

test('Starred calls keep heterogeneous static tuples and instance receivers correctly typed', () => {
    const v=execute(`def display(int n,str text):
    return text+str(n)
pair=(3,"A")
text=display(*pair)
def make_pair():
    return 4,"B"
other=display(*make_pair())
class Adder:
    def __init__(self,int value):
        self.value=value
    def add(self,int a,int b):
        return self.value+a+b
adder=Adder(10)
values=[2,3]
answer=adder.add(*values)
def join(*str words):
    return ":".join(words)
words=["a","b","c"]
last=""
for iteration in range(150):
    last=join(*words)
`,{maxSlices:10000})
    assert.equal(v.text,'A3');assert.equal(v.other,'B4');assert.equal(v.answer,15);assert.equal(v.last,'a:b:c')
})

test('Native starred calls bind defaults, varargs and keyword maps in source order', () => {
    const v=execute(`def total(int first,int second=10,*rest):
    answer=first+second
    for n in rest:
        answer+=n
    return answer
values=[1,2]
options={"second":5}
a=total(*values)
b=total(*[3],**options)
c=total(*list(range(4)))
def later():
    values.append(100)
    return 4
d=total(*values,later())
duplicate=False
try:
    total(*values,**options)
except TypeError:
    duplicate=True
def join(*str parts):
    return "-".join(parts)
parts=["A","B"]
text=join(*parts)
left=right=[1,2]
right.append(3)
`,{maxSlices:12000})
    assert.deepEqual([v.a,v.b,v.c,v.d],[3,8,6,7]);assert.deepEqual(v.values,[1,2,100]);assert.equal(v.duplicate,1);assert.equal(v.text,'A-B');assert.deepEqual(v.left,[1,2,3]);assert.equal(v.left,v.right)
})

test('Nested and starred unpacking preserves values, aliases and iterator consumption', () => {
    const v=execute(`a,(b,c),*rest=(1,("B","C"),4,5)
head,*middle,last=[10,20,30,40]
first,*chars="abc"
left,right="xy"
def unpack():
    value,(name,*tail)=(3,("A","B","C"))
    return name+"".join(tail)
text=unpack()
result=[]
for (x,y),z in [((1,2),3),((4,5),6)]:
    result.append(x+y+z)
computed=[x+sum(tail) for x,*tail in [[1,2,3],[4,5,6]]]
def values():
    for n in range(5):
        yield n
iterator=values()
caught=False
try:
    x,y=iterator
except ValueError:
    caught=True
remaining=next(iterator)
`,{maxSlices:16000})
    assert.deepEqual([v.a,v.b,v.c,v.head,v.last,v.first,v.left,v.right,v.text],[1,'B','C',10,40,'a','x','y','ABC'])
    assert.deepEqual(v.rest,[4,5]);assert.deepEqual(v.middle,[20,30]);assert.deepEqual(v.chars,['b','c']);assert.deepEqual(v.result,[6,15]);assert.deepEqual(v.computed,[6,15])
    assert.equal(v.caught,1);assert.equal(v.remaining,3)
})

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

test('Inlining preserves default and keyword argument snapshots and their side effects',()=>{
    for(const optimization of ['reference','balanced','aggressive']) {
        assert.deepEqual(execute('events=[]\ndef operand(int value):\n    events.append(value)\n    return value\ndef add(int a,int b=4):\n    return a+b\ndef run():\n    first=add(operand(1))\n    second=add(b=operand(2),a=operand(3))\n    return first+second\nanswer=run()',{optimization}),{events:[1,2,3],answer:10})
    }
})

test('Dynamic and literal tuple equality compares matching element schemas and actual lengths',()=>{
    assert.deepEqual(execute('bool same=tuple([1,2])==(1,2)\nbool reversed=(1,2)==tuple([1,2])\nbool different=tuple([1,2])==(1,3)\nbool shorter=tuple([1])==(1,2)\nbool empty=tuple([])==()\nbool typed=tuple(["1"])==(1,)'),{same:1,reversed:1,different:0,shorter:0,empty:1,typed:0})
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
test('Indexed compound arithmetic evaluates indexes once and handles bitwise shifts and powers',() => {
    assert.deepEqual(execute('a=[5,6,7,3,32,2]\na[0]&=3\na[1]|=8\na[2]^=3\na[3]<<=2\na[4]>>=3\na[5]**=5'),{a:[1,14,4,12,4,32]})
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


test('Byte font blitter matches independent transparent pixel rasterization',()=>{
    const fontResult=spawnSync(process.env.TIPY_PYTHON||'python',['-c','import json; from tipy.text_font import FONT,EXTRA; print(json.dumps({**FONT,**EXTRA}))'],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(fontResult.status,0,fontResult.stderr)
    const font=JSON.parse(fontResult.stdout),seed=Uint8Array.from({length:768},(_,i)=>(i*37+91)&255)
    const wrap=n=>((n+32768)&65535)-32768
    const alphabet=Array.from({length:95},(_,i)=>String.fromCharCode(32+i)).join('')
    const grid=alphabet.match(/.{1,12}/g).join('\n')
    const cases=Array.from({length:8},(_,x)=>[grid,x,0])
    for(const [x,y] of [[-6,0],[-4,-1],[-1,-6],[91,60],[95,63],[96,64],[32767,32767],[-32768,-32768]])cases.push(['Wabc!?',x,y])
    cases.push(['A\tB\rC\nD',3,1])
    for(const [text,startX,startY] of cases){
        const expected=Array.from({length:6144},(_,i)=>(seed[i>>3]>>(7-(i&7)))&1)
        let x=startX,y=startY
        for(const char of text){
            if(char==='\n'){x=startX;y=wrap(y+8);continue}
            if(char==='\r'){x=startX;continue}
            if(char==='\t'){x=wrap(x+48-(((x-startX)&65535)%48));continue}
            const glyph=font[char]||font['?']
            for(let row=0;row<7;row++)for(let col=0;col<5;col++){
                const px=wrap(x+col),py=wrap(y+row)
                if((glyph[row]&(16>>col))&&px>=0&&px<96&&py>=0&&py<64)expected[py*96+px]=1
            }
            x=wrap(x+6)
        }
        const m={seedGraph:seed,guardGraph:true,onFrame(r){
            const word=name=>{const a=r.assembly.asm.scopes[0].symbols.get(name).value;return wrap(r.machine.memory[a]|r.machine.memory[a+1]<<8)}
            assert.equal(word('__text_end_x'),x);assert.equal(word('__text_end_y'),y)
        }}
        execute(`print(${JSON.stringify(text)},${startX},${startY})\nrender()`,m)
        assert.deepEqual(Array.from(m.frames[0]),expected,`text at ${startX},${startY}`)
    }
})

test('Register interpolation preserves signed endpoints and fractional rounding',()=>{
    const kernel=spawnSync(process.env.TIPY_PYTHON||'python',['-c','from tipy.wireframe_runtime import routines; print("\\n".join(s for s in routines(1) if s.startswith(("__gfx_wf_umul:","__gfx_wf_lerp:"))))'],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(kernel.status,0,kernel.stderr)
    const cases=[]
    for(const a of [-32768,-7800,-1,0,1,7800,32767])for(const b of [-32768,-1,0,32767])for(const [n,d] of [[0,1],[1,1],[1,2],[2,3],[512,1537]])cases.push([a,b,n,d])
    const code=['org 0x9d95'],data=[]
    for(const [i,args] of cases.entries()){
        for(const value of args)code.push(`ld hl,${value}`,'push hl')
        code.push('call __gfx_wf_lerp',...Array(4).fill('pop bc'),`ld (lerp_${i}),hl`)
        data.push(`lerp_${i}: dw 0`)
    }
    const runner=new Ti84Runner([...code.map(s=>'        '+s),'        ret',...data,kernel.stdout].join('\n'))
    for(let i=0;i<2000&&!runner.done;i++)runner.runSlice()
    assert.ok(runner.done)
    for(const [i,[a,b,n,d]] of cases.entries()){
        const address=runner.assembly.asm.scopes[0].symbols.get(`lerp_${i}`).value
        const value=runner.machine.memory[address]|runner.machine.memory[address+1]<<8
        assert.equal(value,(a+Math.sign(b-a)*Math.floor(Math.abs(b-a)*n/d))&65535,`${a},${b},${n}/${d}`)
    }
})

test('Literal directional lights match native normalization with both quality policies',()=>{
    for(const quality of ['exact','aggressive']){
        const mesh='int model=mesh([(-5,-5,0),(5,-5,0),(0,5,0)],faces=[(0,1,2)])\n'
        const captures=[]
        for(const dynamic of [false,true]){
            const records=[]
            const m={project:true,optimization:'aggressive',onFrame(r){
                const a=r.assembly.asm.scopes[0].symbols.get('__gfx_light_records').value
                records.push(Array.from(r.machine.memory.slice(a,a+48)))
            }}
            const lights=[[1,1,1],[-3,7,-11],[2047,2047,-2047],[17,29,-31]]
            const calls=lights.map(v=>`directional_light(${v[0]}${dynamic?'+int(key_down("left"))':''},${v[1]},${v[2]},12)`).join('\n')
            execute(`set_render_quality("${quality}")\n${mesh}no_lights()\n${calls}\nclear()\ndraw_mesh(model,cull=False,depth_test=False)\nrender()`,m)
            captures.push({records,frames:m.frames})
            assert.equal(m.assembly.includes('__gfx_solid_directional_coeff:'),!dynamic)
        }
        assert.deepEqual(captures[0],captures[1],quality)
    }
})


test('Precomputed directional lights retain registry capacity and validation',()=>{
    const errors=[]
    for(const dynamic of [false,true]){
        const call=`directional_light(1${dynamic?'+int(key_down("left"))':''},2,3)\n`
        assert.throws(()=>execute('no_lights()\n'+call.repeat(5)),error=>{errors.push(error.message);return true})
    }
    assert.equal(errors[0],errors[1])
    assert.throws(()=>execute('directional_light(0,0,0)'),/direction cannot be zero/)
    assert.throws(()=>execute('directional_light(2048,0,0)'),/Light argument must be/)
    assert.throws(()=>execute('directional_light(1,0,0,17)'),/Light argument must be/)
})


test('PUSH buffer clear preserves interrupt state, stack, guards and index registers',()=>{
    const code=spawnSync(process.env.TIPY_PYTHON||'python',['-c','from tipy.graphics_runtime import graphics_assembly; print(graphics_assembly({"clear"}))'],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(code.status,0,code.stderr)
    for(const enabled of [0,1]){
        const r=new Ti84Runner('        org 0x9d95\n        call __gfx_clear\n        ret\n'+code.stdout)
        r.machine.memory.fill(255,0x9340,0x9640);r.machine.memory[0x933f]=165;r.machine.memory[0x9640]=90
        r.machine.z80.regs.iff1=enabled;r.machine.z80.regs.iff2=enabled
        for(let i=0;i<50&&!r.done;i++)r.runSlice()
        assert.ok(r.done);assert.ok(r.machine.memory.slice(0x9340,0x9640).every(value=>value===0))
        assert.equal(r.machine.memory[0x933f],165);assert.equal(r.machine.memory[0x9640],90)
        assert.equal(r.machine.z80.regs.sp,0);assert.equal(r.machine.z80.regs.ix,0);assert.equal(r.machine.z80.regs.iy,0x89f0)
        assert.equal(r.machine.z80.regs.iff1,enabled);assert.equal(r.machine.z80.regs.iff2,enabled)
        assert.ok(r.machine.tStateCount<6000)
    }
})

test('Compiled small-model transforms match the general vertex loop across matrices and cameras',()=>{
    const draws=['draw_mesh(model,cull=False,depth_test=False)','draw_mesh(model,x=3,y=-2,z=4,yaw=31,pitch=17,roll=9,size=301,cull=False,depth_test=False)',
        'camera(0,0,20,0,0,0)\ndraw_mesh(model,cull=False,depth_test=False)','camera(-10,8,25,0,0,0)\ndraw_mesh(model,yaw=91,size=512,cull=False,depth_test=False)']
    const solidCube=meshCube.replace('])\n','],faces=[(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),(3,2,6),(3,6,7),(0,3,7),(0,7,4),(1,5,6),(1,6,2)])\n')
    const source=solidCube+'init_camera(75,2,120)\n'+draws.map(draw=>'clear()\n'+draw+'\nrender()').join('\n')
    const fast={project:true,optimization:'aggressive',guardGraph:true,trackStack:true},reference={...fast,genericVertices:true}
    execute(source,fast);execute(source,reference)
    assert.deepEqual(fast.frames,reference.frames)
    assert.ok(fast.assembly.includes('__gfx_mesh_vertex_plan:'));assert.ok(!reference.assembly.includes('__gfx_mesh_vertex_plan:'))
    const table=fast.assembly.includes('__gfx_solid_fast_span_tables:')
    if(table){
        const r=new Ti84Runner(fast.assembly)
        assert.equal(r.assembly.asm.scopes[0].symbols.get('__gfx_solid_fast_span_tables').value&255,0)
    }
})

test('Sparse blank-box rasterization preserves opaque fills, boundaries and subsequent meshes',()=>{
    const prefix=readFileSync(resolve('../../examples/lumen_run.tip'),'utf8').split('# Small parabolic')[0]
    const scene='def scene(int x,int y,int z,int tx,int ty,int tz,int brightness) -> void:\n    clear()\n    camera(x,y,z,tx,ty,tz)\n    no_lights()\n    ambient_light(brightness)\n    draw_mesh(room,cull=False,depth_test=False,outline=True)\n    draw_mesh(beacon,x=2,y=-8,z=-12,cull=False,shaded=False,depth_test=False,outline=True)\n    render()\n'
    const poses=[[0,0,0,0,0,-10],[-17,0,17,0,4,-17],[17,0,17,-17,-3,0],[0,9,0,0,0,-12],[0,-7,0,5,0,-12],[18,0,0,0,0,-10],[25,0,0,0,0,-10]]
    const source=prefix+scene+'init_camera(75,2,90)\nfor brightness in range(12,17):\n'+poses.map(p=>'    scene('+p.join(',')+',brightness)').join('\n')
    const fast={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:40000},reference={...fast,opaqueReference:true}
    execute(source,fast);execute(source,reference)
    assert.equal(fast.frames.length,35)
    assert.ok(fast.assembly.includes('__gfx_solid_transparent_white:'))
    assert.deepEqual(fast.frames,reference.frames)
})


test('Finite function tables preserve signed periodic input and the caller stack',()=>{
    const source='def wave(int a) -> int:\n    a=a&31\n    if a>=16:\n        return -(a-16)*(a-16)\n    return a*a\nint offset=int(key_down("left"))\nint total=0\nfor a in range(-40,40):\n    total+=wave(a+offset)\npixel_on(total&63,0)\nrender()'
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true}
    const values=execute(source,m)
    let total=0
    for(let i=-40;i<40;i++){const a=i&31;total+=a>=16?-(a-16)*(a-16):a*a}
    assert.equal(values.total,((total+32768)&65535)-32768)
    assert.ok(m.assembly.includes('__fn_0_table:'))
    assert.ok(m.report.some(pass=>pass.pass==='finite-function-tables'&&pass.specialized===1))
})

test('Level camera reciprocal roots stay within one Q8 unit and reject other domains',()=>{
    const kernels=spawnSync(process.env.TIPY_PYTHON||'python',['-c','from tipy.mesh_runtime import _level_fast; from tipy.wireframe_runtime import routines; print(_level_fast()); print(next(s for s in routines(1) if s.startswith("__gfx_wf_umul:")))'],{cwd:resolve('../..'),encoding:'utf8'})
    assert.equal(kernels.status,0,kernels.stderr)
    const cases=Array.from({length:64},(_,i)=>[Math.round(16*Math.sin(i*Math.PI/32)),Math.round(16*Math.cos(i*Math.PI/32))])
    cases.push([0,0],[1,1],[20,20],[-20,20],[120,0],[-120,0],[32767,-32768])
    const code=['org 0x9d95'],data=[]
    for(const [i,[x,z]] of cases.entries()){
        code.push(`ld hl,${x}`,'push hl',`ld hl,${z}`,'push hl','call __gfx_mesh_level_fast','pop bc','pop bc',`ld (level_ok_${i}),hl`)
        data.push(`level_ok_${i}: dw 0`)
        for(let j=0;j<9;j++){code.push(`ld hl,(__gfx_mesh_basis+${j*2})`,`ld (level_${i}_${j}),hl`);data.push(`level_${i}_${j}: dw 0`)}
    }
    const r=new Ti84Runner([...code.map(line=>'        '+line),'        ret',...data,'__gfx_mesh_basis: defs 18,0',kernels.stdout].join('\n'))
    for(let i=0;i<1000&&!r.done;i++)r.runSlice()
    assert.ok(r.done);assert.equal(r.machine.z80.regs.sp,0);assert.equal(r.machine.z80.regs.ix,0);assert.equal(r.machine.z80.regs.iy,0x89f0)
    const word=name=>{const a=r.assembly.asm.scopes[0].symbols.get(name).value;const v=r.machine.memory[a]|r.machine.memory[a+1]<<8;return v>32767?v-65536:v}
    let previous=Array(9).fill(0)
    for(const [i,[x,z]] of cases.entries()){
        const sum=x*x+z*z,valid=Math.abs(x)<=20&&Math.abs(z)<=20&&sum>=192&&sum<320
        assert.equal(word(`level_ok_${i}`),Number(valid))
        const actual=Array.from({length:9},(_,j)=>word(`level_${i}_${j}`))
        if(valid){
            const nx=x*256/Math.sqrt(sum),nz=z*256/Math.sqrt(sum),expected=[-nz,0,nx,0,256,0,nx,0,nz]
            for(let j=0;j<9;j++)assert.ok(Math.abs(actual[j]-expected[j])<=1,`${x},${z}: basis ${j}`)
        }else assert.deepEqual(actual,previous)
        previous=actual
    }
})


test('LUMEN static-light renderer averages at least 20 FPS on the fixed-pose native benchmark',()=>{
    const source=readFileSync(resolve('../../experiments/fixtures/lumen_static.tip'),'utf8').replace('int yaw=0','int yaw=3').replace('yaw=(yaw+turn*(turn_remainder//3))&127','yaw=(yaw+turn*2)&127')
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000,afterFrameKeys:Array.from({length:8},(_,i)=>i===7?[15]:[3])}
    execute(source,m)
    const frames=m.frameTimes.slice(1).map((time,i)=>time-m.frameTimes[i]),mean=frames.reduce((a,b)=>a+b,0)/frames.length
    assert.equal(frames.length,7)
    assert.ok(mean<=750000,`Mean ${mean} T-states exceeds the 20 FPS budget at 15 MHz`)
    assert.ok(m.bytes<=24169);assert.ok(m.stackBytes<1024)
})


test('LUMEN sunlight orbits above the room without input and changes its shading',()=>{
    const captured=[],m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:40000,
        onFrame(r,result){
            const word=name=>{const a=r.assembly.asm.scopes[0].symbols.get(result.symbols[name].label).value;const v=r.machine.memory[a]|r.machine.memory[a+1]<<8;return v>32767?v-65536:v}
            const phase=word('sun_phase')
            if(captured.length<4&&phase>=[0,32,64,96][captured.length]){
                captured.push({phase,x:word('sun_x'),z:word('sun_z'),pixels:r.lcd.pixels.slice()})
            }
            assert.equal(word('px'),-160);assert.equal(word('pz'),160)
            if(phase>=96)r.setKey(15,true)
        }}
    execute(readFileSync(resolve('../../examples/lumen_run.tip'),'utf8'),m)
    assert.equal(captured.length,4)
    assert.deepEqual(captured.map(f=>[f.x,f.z]),[[0,24],[24,0],[0,-24],[-24,0]])
    for(let i=1;i<captured.length;i++)assert.notDeepEqual(captured[i].pixels,captured[0].pixels)
    assert.ok(m.bytes<=24169)
})


test('Projected floor shadows match explicitly projected geometry and preserve graphics memory',()=>{
    const vertices=[[-2,0,-2],[2,0,-2],[2,0,2],[-2,0,2],[0,6,0]]
    const faces=[[0,1,4,0],[1,2,4,0],[2,3,4,0],[3,0,4,0]]
    const model=(name,points)=>{const valid=faces.filter(f=>{const [a,b,c]=f.map(i=>points[i]);return [0,1,2].some(i=>(b[(i+1)%3]-a[(i+1)%3])*(c[(i+2)%3]-a[(i+2)%3])!==(b[(i+2)%3]-a[(i+2)%3])*(c[(i+1)%3]-a[(i+1)%3]))});return `int ${name}=mesh([${points.map(v=>'('+v.join(',')+')').join(',')}],faces=[${valid.map(f=>'('+f.join(',')+')').join(',')}])\n`}
    const setup='init_camera(75,2,90)\ncamera(16,14,24,0,0,0)\nclear()\n'
    for(const light of [[10,24,6],[-18,24,-12],[0,30,0]]){
        const projected=vertices.map(([x,y,z])=>[light[0]+Math.trunc((x-light[0])*light[1]/(light[1]-y)),0,light[2]+Math.trunc((z-light[2])*light[1]/(light[1]-y))])
        const actual={project:true,guardGraph:true,trackStack:true},reference={project:true,guardGraph:true}
        execute(model('m',vertices)+setup+`draw_shadow(m,${light.join(',')})\nrender()`,actual)
        execute(model('m',projected)+setup+'draw_mesh(m,filled=True,cull=False,shaded=False,depth_test=False,outline=False)\nrender()',reference)
        assert.ok(actual.screen.some(Boolean),'Shadow must draw visible pixels')
        assert.deepEqual(actual.screen,reference.screen,`Projection for ${light}`)
    }
})

test('Shadows are omitted for lights at/below the receiver or below the caster',()=>{
    for(const height of [-2,0,3,6]){
        const m={project:true,guardGraph:true,trackStack:true}
        execute(`int m=mesh([(-2,0,-2),(2,0,-2),(0,6,0)],faces=[(0,1,2)])
init_camera(75,2,90)
camera(16,14,24,0,0,0)
clear()
draw_shadow(m,10,${height},6)
render()`,m)
        assert.equal(m.screen.some(Boolean),false)
    }
})

test('LUMEN moving sunlight and shadows average at least 20 FPS at native 15 MHz',()=>{
    const source=readFileSync(resolve('../../examples/lumen_run.tip'),'utf8').replace('int yaw=0','int yaw=3').replace('yaw=(yaw+turn*(turn_remainder//3))&127','yaw=(yaw+turn*2)&127')
    const m={project:true,optimization:'aggressive',guardGraph:true,trackStack:true,maxSlices:20000,afterFrameKeys:Array.from({length:8},(_,i)=>i===7?[15]:[3])}
    execute(source,m)
    const frames=m.frameTimes.slice(1).map((time,i)=>time-m.frameTimes[i])
    assert.equal(frames.length,7)
    const mean=frames.reduce((a,b)=>a+b,0)/frames.length
    assert.ok(mean<=750000,`Mean ${mean} T-states exceeds 20 FPS at 15 MHz`)
    assert.ok(m.assembly.includes('__gfx_mesh_shadow:'))
    assert.ok(m.bytes<=24169)
    assert.ok(m.stackBytes<1024)
})


test('Prepared shadow vertices agree with the generic path across light positions and receiver heights',()=>{
    for(const floor of [0,-8])for(const light of [-16,0,18]){
        const source=`int m=mesh([(-2,0,-2),(2,0,-2),(2,0,2),(-2,0,2),(0,6,0)],faces=[(0,1,4,0),(1,2,4,0),(2,3,4,0),(3,0,4,0)])
init_camera(75,2,90)
camera(16,14,24,0,0,0)
clear()
draw_shadow(m,${light},24,10,floor_y=${floor},x=2,y=${floor},z=-2)
draw_mesh(m,x=2,y=${floor},z=-2,filled=True,cull=False,shaded=False,depth_test=False,outline=False)
render()`
        const fast={project:true,guardGraph:true,trackStack:true},generic={project:true,genericVertices:true,guardGraph:true,trackStack:true}
        execute(source,fast);execute(source,generic)
        assert.ok(fast.assembly.includes('__gfx_mesh_prepare_only:'))
        assert.deepEqual(fast.screen,generic.screen,`Light ${light}, floor ${floor}`)
    }
})


test('Packed collision maps handle signed bounds, touching edges, empty maps and player radius',()=>{
    const m={project:true,guardGraph:true,trackStack:true}
    const v=execute(`int walls=collision_map([(-2,-10,2,10),(10,10,12,12)])
bool inside=collides(walls,0,0,radius=0)
bool edge=collides(walls,-2,10,radius=0)
bool outside=collides(walls,-3,0,radius=0)
bool touching=collides(walls,-3,0,radius=1)
bool second=collides(walls,11,11,radius=0)
int empty=collision_map([])
bool empty_hit=collides(empty,0,0)`,m)
    assert.deepEqual([v.inside,v.edge,v.outside,v.touching,v.second,v.empty_hit],[1,1,0,1,1,0])
    assert.ok(!m.assembly.includes('__heap_start:'))
    assert.throws(()=>execute('int m=collision_map([])\nbool hit=collides(m,0,0,radius=-1)',{project:true}),/Runtime error/)
})

test('Scalar argument snapshots preserve keyword order, defaults and later argument side effects',()=>{
    const v=execute(`int state=3
def change() -> int:
    print("X",0,0)
    return 8
def subtract(int a,int b=2) -> int:
    return a-b
int first=subtract(b=change(),a=state)
int defaulted=subtract(10)
int keyword=subtract(b=5,a=12)`,{project:true,optimization:'aggressive'})
    assert.deepEqual([v.first,v.defaulted,v.keyword],[-5,8,7])
})

test('Gallery walks through the doorway, blocks divider walls and the imported panel',()=>{
    const asset=readFileSync(resolve('../../examples/lumen_gallery.obj'),'utf8')
    const original=readFileSync(resolve('../../examples/lumen_gallery.tip'),'utf8')
    for(const pose of [{z:3,yaw:32,stop:8,blocked:false},{z:10,yaw:32,stop:-2,blocked:true},{z:0,yaw:32,stop:-12,blocked:true}]){
        const source=original.replace('FirstPerson(x=-18, z=10, yaw=45)',`FirstPerson(x=-18, z=${pose.z}, yaw=90)`)
        const m={project:true,optimization:'aggressive',assets:{'lumen_gallery.obj':asset},guardGraph:true,trackStack:true,maxSlices:30000,
            afterFrameKeys:Array.from({length:120},(_,i)=>i===119?[15]:[4])}
        const v=execute(source,m)
        if(pose.blocked)assert.ok(v.player._x<=pose.stop*16,`Stopped by collider at ${pose.stop}`)
        else assert.ok(v.player._x>pose.stop*16,'Walk through the gap into the east room')
        assert.equal(v.player._z,pose.z*16)
        assert.ok(m.bytes<=24169);assert.ok(m.stackBytes<1024)
    }
})

test('Gallery picks up its crystal and can finish at the west entrance',()=>{
    const source=readFileSync(resolve('../../examples/lumen_gallery.tip'),'utf8').replace('FirstPerson(x=-18, z=10, yaw=45)','FirstPerson(x=14, z=-8, yaw=45)')
    const m={project:true,optimization:'aggressive',assets:{'lumen_gallery.obj':readFileSync(resolve('../../examples/lumen_gallery.obj'),'utf8')},guardGraph:true,trackStack:true,maxSlices:30000,
        onFrame(r,result,count){
            const set=(name,value)=>{const [record,field]=name.split('.');const symbol=field?result.record_symbols[record][field]:result.symbols[name];const a=r.assembly.asm.scopes[0].symbols.get(symbol.label).value;r.machine.memory[a]=value&255;r.machine.memory[a+1]=(value>>8)&255}
            if(count===1){set('player._x',-20*16);set('player._z',10*16)}
            if(count===2)r.setKey(15,true)
        }}
    const v=execute(source,m)
    assert.equal(v.collected,1);assert.equal(v.won,1)
    assert.notDeepEqual(m.frames[0],m.frames[1])
})


test('Conservative instance side-plane rejection preserves visible pixels at camera boundaries',()=>{
    const header='int m=mesh([(-3,-3,-3),(3,-3,-3),(3,3,-3),(-3,3,-3),(-3,-3,3),(3,-3,3),(3,3,3),(-3,3,3)],faces=[(0,1,2),(0,2,3),(4,7,6),(4,6,5),(0,4,5),(0,5,1),(3,2,6),(3,6,7)])\ninit_camera(75,2,90)\ncamera(0,0,0,0,0,-16)\n'
    for(const size of [128,256,512]){
        const source=header+[-50,-24,-10,0,10,24,50].map(x=>`clear()\ndraw_mesh(m,x=${x},y=3,z=-20,size=${size},cull=False,depth_test=False)\nrender()`).join('\n')
        const actual={project:true,guardGraph:true},reference={project:true,noFrustum:true,guardGraph:true}
        execute(source,actual);execute(source,reference)
        assert.deepEqual(actual.frames,reference.frames,`Size ${size}`)
    }
})


test('Demand-linked font subsets match the complete font for clipped text, numbers and booleans',()=>{
    const source='clear()\nprint("FIND CRYSTAL",1,1)\nprint("WEST: 1/2?",-5,57)\nprint(-32768,61,8)\nprint(True,0,20)\nrender()'
    const compact={project:true,guardGraph:true},full={project:true,fullFont:true,guardGraph:true}
    execute(source,compact);execute(source,full)
    assert.deepEqual(compact.screen,full.screen)
    assert.ok(compact.bytes<full.bytes)
})


test('Gallery averages at least 20 FPS with its OBJ props, collision checks and orbiting shadows',()=>{
    const m={project:true,optimization:'aggressive',assets:{'lumen_gallery.obj':readFileSync(resolve('../../examples/lumen_gallery.obj'),'utf8')},guardGraph:true,trackStack:true,maxSlices:40000,
        afterFrameKeys:Array.from({length:128},(_,i)=>i===127?[15]:[3,4])}
    execute(readFileSync(resolve('../../examples/lumen_gallery.tip'),'utf8'),m)
    const times=m.frameTimes.slice(1).map((time,i)=>time-m.frameTimes[i])
    const mean=times.reduce((a,b)=>a+b,0)/times.length
    assert.ok(mean<=750000,`Mean ${mean} T-states exceeds 20 FPS at 15 MHz`)
    assert.ok(m.bytes<=24169);assert.ok(m.stackBytes<1024)
    assert.ok(!m.assembly.includes('__heap_start:'))
})


test('Sub-unit diagonal camera motion advances continuously without alternating whole-unit axis jumps',()=>{
    const poses=[]
    const m={project:true,guardGraph:true,trackStack:true,onFrame(r){
        const symbols=r.assembly.asm.scopes[0].symbols,mem=r.machine.memory
        const word=a=>{const v=mem[a]|mem[a+1]<<8;return v>32767?v-65536:v}
        const base=symbols.get('__gfx_mesh_basis').value,center=symbols.get('__gfx_mesh_center').value
        poses.push({basis:Array.from({length:9},(_,i)=>word(base+i*2)),center:Array.from({length:3},(_,i)=>word(center+i*2))})
    }}
    execute(`int model=mesh([(-4,-4,0),(4,-4,0),(4,4,0),(-4,4,0)],faces=[(0,1,2,0),(0,2,3,0)])
init_camera(75,2,90)
for phase in range(17):
    camera(-288+phase,0,160-phase,-96+phase,0,-32-phase,coordinate_scale=16)
    clear()
    draw_mesh(model,cull=False,shaded=False,depth_test=False)
    render()`,m)
    assert.equal(poses.length,17)
    for(let i=0;i<poses.length;i++){
        assert.deepEqual(poses[i].basis,poses[0].basis,'Translation must not wobble camera direction')
        const eye=[(-288+i)/16,0,(160-i)/16]
        for(let axis=0;axis<3;axis++){
            const expected=-eye.reduce((sum,value,j)=>sum+value*poses[i].basis[axis*3+j],0)
            assert.ok(Math.abs(poses[i].center[axis]-expected)<=1,`Phase ${i}, axis ${axis}: preserve fractional translation`)
        }
    }
    const side=poses.map(p=>p.center[0])
    assert.ok(Math.max(...side)-Math.min(...side)<=1,'Straight diagonal walk must not staircase sideways')
    for(let i=1;i<poses.length;i++)assert.ok(Math.abs(poses[i].center[2]-poses[i-1].center[2])<32,'Each 1/16-unit step stays below a whole-unit jump')
    assert.notDeepEqual(m.frames[0],m.frames.at(-1))
})

test('Fractional camera modes reset correctly when switching back to ordinary camera coordinates',()=>{
    const prefix='int model=mesh([(-2,-2,0),(2,-2,0),(2,2,0),(-2,2,0)],faces=[(0,1,2,0),(0,2,3,0)])\ninit_camera(75,1,32)\n'
    const draw='clear()\ndraw_mesh(model,cull=False,shaded=False,depth_test=False)\nrender()\n'
    const mixed={project:true,guardGraph:true,trackStack:true},plain={project:true,guardGraph:true}
    execute(prefix+'camera(-20,0,136,-20,0,-120,coordinate_scale=16)\n'+draw+'camera(-2,0,8,-2,0,-8)\n'+draw,mixed)
    execute(prefix+'camera(-2,0,8,-2,0,-8)\n'+draw,plain)
    assert.deepEqual(mixed.frames[1],plain.screen)
    assert.notDeepEqual(mixed.frames[0],plain.screen)
})

test('Scaled collision tests preserve sub-unit edge clearance on both sides of an obstacle',()=>{
    const v=execute(`int walls=collision_map([(-2,-2,2,2)])
bool left_clear=collides(walls,-49,0,coordinate_scale=16)
bool left_touch=collides(walls,-48,0,coordinate_scale=16)
bool right_touch=collides(walls,48,0,coordinate_scale=16)
bool right_clear=collides(walls,49,0,coordinate_scale=16)
bool positive_clear=collides(walls,33,0,radius=0,coordinate_scale=16)
bool negative_clear=collides(walls,-33,0,radius=0,coordinate_scale=16)`,{project:true,guardGraph:true,trackStack:true})
    assert.deepEqual([v.left_clear,v.left_touch,v.right_touch,v.right_clear,v.positive_clear,v.negative_clear],[0,1,1,0,0,0])
})

test('Gallery forward diagonal motion keeps a constant camera direction and balanced signed movement',()=>{
    const poses=[]
    const m={project:true,optimization:'aggressive',assets:{'lumen_gallery.obj':readFileSync(resolve('../../examples/lumen_gallery.obj'),'utf8')},guardGraph:true,trackStack:true,maxSlices:30000,
        afterFrameKeys:Array.from({length:16},(_,i)=>i===15?[15]:[4]),onFrame(r,result){
            const symbols=r.assembly.asm.scopes[0].symbols,mem=r.machine.memory
            const word=a=>{const v=mem[a]|mem[a+1]<<8;return v>32767?v-65536:v}
            const read=name=>{const [record,field]=name.split('.');return word(symbols.get((field?result.record_symbols[record][field]:result.symbols[name]).label).value)}
            poses.push({x:read('player._x'),z:read('player._z'),side:word(symbols.get('__gfx_mesh_center').value),basis:Array.from({length:9},(_,i)=>word(symbols.get('__gfx_mesh_basis').value+i*2))})
        }}
    execute(readFileSync(resolve('../../examples/lumen_gallery.tip'),'utf8'),m)
    const totals=poses.map(p=>p.x+p.z),side=poses.map(p=>p.side)
    assert.ok(poses.at(-1).x>poses[0].x&&poses.at(-1).z<poses[0].z)
    assert.ok(Math.max(...totals)-Math.min(...totals)<=1,'Signed division carries fractional movement instead of introducing drift')
    assert.ok(Math.max(...side)-Math.min(...side)<=12,'Panel stays centered during straight diagonal movement')
    for(const pose of poses)assert.deepEqual(pose.basis,poses[0].basis)
})


test('Fractional shadow rendering agrees between compiled and generic mesh transforms',()=>{
    const source=`int model=mesh([(-2,0,-2),(2,0,-2),(2,0,2),(-2,0,2),(0,5,0)],faces=[(0,1,4,2),(1,2,4,2),(2,3,4,2),(3,0,4,2)])
init_camera(75,1,80)
for phase in range(3):
    camera(-64+phase,128+phase,384-phase,0,0,0,coordinate_scale=16)
    clear()
    draw_shadow(model,15,24,0)
    draw_mesh(model,cull=False,shaded=False,depth_test=False,outline=True)
    render()`
    const fast={project:true,guardGraph:true,trackStack:true},generic={project:true,genericVertices:true,guardGraph:true,trackStack:true}
    execute(source,fast);execute(source,generic)
    assert.ok(fast.screen.some(Boolean))
    assert.deepEqual(fast.frames,generic.frames)
})

test('Fractional coordinate scales share consistent collision radius and ordinary camera defaults',()=>{
    for(const scale of [2,4,8,16]){
        const v=execute(`int walls=collision_map([(-2,-2,2,2)])
bool touching=collides(walls,${3*scale},0,coordinate_scale=${scale})
bool outside=collides(walls,${3*scale+1},0,coordinate_scale=${scale})`,{project:true,guardGraph:true,trackStack:true})
        assert.deepEqual([v.touching,v.outside],[1,0])
    }
    const prefix='int m=mesh([(-8,-8,0),(8,-8,0),(0,8,0)],faces=[(0,1,2,0)])\ninit_camera(75,1,120)\n'
    const draw='clear()\ndraw_mesh(m,cull=False,shaded=False,depth_test=False)\nrender()'
    const scaled={project:true,guardGraph:true},ordinary={project:true,guardGraph:true}
    execute(prefix+'camera(coordinate_scale=16)\n'+draw,scaled)
    execute(prefix+'camera()\n'+draw,ordinary)
    assert.ok(scaled.screen.some(Boolean))
    assert.deepEqual(scaled.screen,ordinary.screen)
})


test('Game helpers preserve fractional world coordinates and independent player bindings',()=>{
    const v=execute(`int first=first_person(x=-1.5,z=2.25,yaw=45)
int second=first_person(x=10,z=-6,yaw=90)
bool close=player_near(first,-1.5,2.25,radius=0.5)
bool boundary=player_near(first,-2,2.25,radius=0.5)
bool distant=player_near(second,-1.5,2.25)
bool other=player_near(second,10,-6)`,{project:true,guardGraph:true,trackStack:true})
    assert.deepEqual([v.close,v.boundary,v.distant,v.other],[1,0,0,1])
})

test('Clear rectangle clips to the LCD and preserves neighboring pixels',()=>{
    const source=`clear_rect(-5,-4,110,7)
clear_rect(90,60,20,10)
clear_rect(-8,20,10,3)
clear_rect(0,30,96,0)
clear_rect(0,64,96,5)
render()`
    const m={project:true,guardGraph:true,trackStack:true,seedGraph:new Uint8Array(768).fill(255)}
    execute(source,m)
    for(let y=0;y<64;y++)for(let x=0;x<96;x++){
        const cleared=y<3 || x>=90&&y>=60 || x<2&&y>=20&&y<23
        assert.equal(m.screen[y*96+x],cleared?0:1,`Pixel ${x},${y}`)
    }
    assert.ok(!m.assembly.includes('__heap_start:'))
})

test('Game helpers stop the frame timer and restore the initial CPU mode on normal and fatal exits',()=>{
    const scene=`int gallery=load_scene("level.obj",background="room")
int player=first_person()
update_player(player,gallery)
clear()
render()
`
    const asset='o room\nv -4 -4 -8\nv 4 -4 -8\nv 0 4 -8\nf 1 2 3\n'
    for(const initialCpuMode of [0,1]){
        let runner
        const m={project:true,guardGraph:true,trackStack:true,initialCpuMode,assets:{'level.obj':asset},onFrame(r){runner=r}}
        execute(scene,m)
        assert.equal(runner.clockHz,initialCpuMode?15000000:6000000)
        assert.equal(runner.frameTimer.clock,0)
        runner=null
        assert.throws(()=>execute(scene+'assert False',m),/Runtime error/)
        assert.equal(runner.clockHz,initialCpuMode?15000000:6000000)
        assert.equal(runner.frameTimer.clock,0)
    }
})


test('Source record inlining preserves argument snapshots, local scopes, loops and mutable defaults',()=>{
    const source=`int value=100
class Body:
    def __init__(self):
        self.x=9
        self.y=0
    def reset(self):
        self.x=0
    def apply(self,int dx):
        self.reset()
        self.y+=dx
    def repeat(self):
        value=4
        for i in range(2):
            self.y+=value
    def step(self,int amount=1):
        amount+=1
        self.y+=amount
body=Body()
body.apply(body.x)
body.repeat()
body.step()
int result=body.y`
    for(const optimization of ['reference','aggressive']){
        const m={project:true,optimization,trackStack:true}
        const v=execute(source+'\nclear()\nprint(result,0,0)\nrender()',m)
        const expected={project:true}
        execute('clear()\nprint(19,0,0)\nrender()',expected)
        assert.deepEqual(m.screen,expected.screen)
        if('result' in v)assert.equal(v.result,19)
        if('value' in v)assert.equal(v.value,100)
        assert.ok(!m.assembly.includes('__heap_start:'))
    }
})

test('Toolkit source modules are editable and independent record instances do not alias',()=>{
    const m={project:true,assets:{'geometry.tip':'class Vector2:\n    def __init__(self,int x=0,int y=0):\n        self.x=x+1\n        self.y=y\n    def move_ip(self,int x,int y):\n        self.x+=x\n        self.y+=y\n'},trackStack:true}
    const v=execute('from geometry import Vector2\na=Vector2(1,2)\nb=Vector2(10,20)\na.move_ip(3,4)\nint ax=a.x\nint ay=a.y\nint bx=b.x\nint by=b.y',m)
    assert.deepEqual([v.ax,v.ay,v.bx,v.by],[5,6,11,20])
    assert.ok(!m.assembly.includes('__heap_start:'))
})

test('Scalar records preserve computed boolean fields and byte argument widening',()=>{
    const source=`import time
class Flags:
    def __init__(self):
        self.active=time.frame_ticks()>0
        self.count=17
    def set_active(self,bool active):
        self.active=active
    def add_one(self,int value)->int:
        return value+1
flags=Flags()
flags.set_active(True)
u8 byte_value=255
int widened=flags.add_one(byte_value)
bool active=flags.active
int count=flags.count`
    const m={project:true,optimization:'reference',trackStack:true}
    const v=execute(source,m)
    assert.deepEqual([v.widened,v.active,v.count],[256,1,17])
    assert.equal(v.flags.active,1)
    assert.ok(!m.assembly.includes('__heap_start:'))
})

test('Pygame-level draw helpers and GX framebuffer access share pixels without a heap',()=>{
    const m={project:true,guardGraph:true,trackStack:true}
    const v=execute(`import gfx
import gx
gfx.clear()
int width=3+int(gfx.key_down("right"))
gfx.draw_rect(4,5,width,2)
gfx.draw_rect(4,8,0,3)
gfx.draw_circle(15,10,2,filled=False)
gfx.draw_line(20,20,22,22)
gx.set_buffer_byte(0,0xAA)
int packed=gx.get_buffer_byte(0)
bool a=gfx.get_pixel(0,0)
bool b=gfx.get_pixel(1,0)
bool clipped=gfx.get_pixel(-1,0)
bool inside=gfx.get_pixel(5,5)
bool outside=gfx.get_pixel(5,8)
gfx.render()`,m)
    assert.deepEqual([v.packed,v.a,v.b,v.clipped,v.inside,v.outside],[170,1,0,0,1,0])
    assert.ok(!m.assembly.includes('__heap_start:'))
    assert.equal(m.screen[20*96+20],1)
    assert.equal(m.screen[22*96+22],1)
})

test('GX view matrices render the same mesh as the convenience camera',()=>{
    const header=`import gfx
import gx
int model=gx.mesh([(-6,-6,0),(6,-6,0),(6,6,0),(-6,6,0)],faces=[(0,1,2,7),(0,2,3,7)])
gx.set_projection(75,2,90)
`
    const draw='gfx.clear()\ngx.draw_mesh(model,cull=False,shaded=False,depth_test=False,outline=True)\ngx.present()'
    const explicit={project:true,guardGraph:true,trackStack:true},convenient={project:true,guardGraph:true,trackStack:true}
    execute(header+'gx.load_view_matrix(0,0,24,256,0,0,0,256,0,0,0,-256)\n'+draw,explicit)
    execute(header+'gfx.camera(0,0,24,0,0,0)\n'+draw,convenient)
    assert.deepEqual(explicit.screen,convenient.screen)
    assert.ok(explicit.screen.some(Boolean))
    for(const source of ['import gx\ngx.get_buffer_byte(-1)','import gx\ngx.set_buffer_byte(768,0)','import gx\ngx.set_buffer_byte(0,256)','import gx\ngx.load_view_matrix(0,0,0,257,0,0,0,256,0,0,0,-256)']){
        assert.throws(()=>execute(source,{project:true}),/Runtime error/)
    }
})

test('Raw framebuffer writes retain observable draw order across presents',()=>{
    const m={project:true,guardGraph:true}
    execute('import gx\ngx.clear()\ngx.set_buffer_byte(0,0x80)\ngx.present()\ngx.set_buffer_byte(0,0)\ngx.present()',m)
    assert.equal(m.frames.length,2)
    assert.equal(m.frames[0][0],1)
    assert.equal(m.frames[1][0],0)
})


test('Toolkit Clock reports milliseconds and limits frames without owning the application loop',()=>{
    const m={project:true,guardGraph:true,trackStack:true,maxSlices:30000}
    const v=execute(`import gfx
from timing import Clock
gfx.init()
clock=Clock()
int elapsed=0
for frame in range(20):
    elapsed=clock.tick(60)
    gfx.clear()
    gfx.pixel_on(frame,10)
    gfx.flip()
gfx.quit()`,m)
    const times=m.frameTimes.slice(1).map((time,i)=>time-m.frameTimes[i])
    const mean=times.reduce((a,b)=>a+b,0)/times.length
    assert.ok(mean>220000&&mean<280000,`60 FPS timing at 15 MHz: ${mean} T-states`)
    assert.ok(v.elapsed>=15&&v.elapsed<=24)
    assert.equal(m.frames.length,20)
    assert.equal(m.clockHz,6000000)
    assert.ok(!m.assembly.includes('__heap_start:'))
})

test('Graphics session init and quit are idempotent and restore the original CPU mode',()=>{
    for(const initialCpuMode of [0,1]){
        const m={project:true,initialCpuMode}
        execute('import gfx\ngfx.quit()\ngfx.init()\ngfx.init()\ngfx.quit()\ngfx.quit()',m)
        assert.equal(m.clockHz,initialCpuMode?15000000:6000000)
    }
})


test('Scene visibility and parameterized drawing work in ordinary user functions',()=>{
    const asset='o prop\nv -3 0 0\nv 3 0 0\nv 3 5 0\nv -3 5 0\nf 1 2 3 4\n'
    const m={project:true,assets:{'model.obj':asset},guardGraph:true,trackStack:true,maxSlices:10000}
    execute(`import gfx
int group=gfx.load_scene("model.obj",unlit=["prop"])
gfx.init_camera(75,2,90)
gfx.camera(0,8,24,0,2,0)
def draw(int light_x,int light_z):
    gfx.draw_scene(group,shadow_light=(light_x,24,light_z))
def hide():
    gfx.set_visible(group,"prop",False)
gfx.clear()
draw(10,8)
gfx.render()
hide()
gfx.clear()
draw(10,8)
gfx.render()`,m)
    assert.ok(m.frames[0].some(Boolean))
    assert.equal(m.frames[1].some(Boolean),false)
})

test('Drawing helper keyword arguments execute once in source order',()=>{
    const m={project:true,guardGraph:true}
    const v=execute(`import gfx
int calls=[0]
def next_value()->int:
    calls[0]+=1
    return calls[0]
gfx.clear()
gfx.draw_circle(radius=next_value(),x=next_value(),y=next_value())
int count=calls[0]
gfx.render()`,m)
    assert.equal(v.count,3)
    const expected={project:true}
    execute('clear()\n__tipy_native_circle(2,3,1,True)\nrender()',expected)
    assert.deepEqual(m.screen,expected.screen)
})
