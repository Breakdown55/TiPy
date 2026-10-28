// Execute the gallery's emitted Z80, including its imported OBJ asset.
import {readFileSync, writeFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {spawnSync} from 'node:child_process'
const runner=resolve('asm-editor/ti84-runner')
const tests=readFileSync(resolve(runner,'dist/python-compiler.test.mjs'),'utf8')
let harness=tests.slice(0,tests.indexOf('test("Perspective camera'))
harness+=`
const {writeFileSync:save}=await import('node:fs');
const scene=readFileSync(resolve2('../../examples/lumen_gallery.tip'),'utf8');
const count=Number(process.env.TIPY_GALLERY_FRAMES||64);
const m={project:true,optimization:'aggressive',assets:{'lumen_gallery.obj':readFileSync(resolve2('../../examples/lumen_gallery.obj'),'utf8')},guardGraph:true,trackStack:true,maxSlices:40000,
 afterFrameKeys:Array.from({length:count},(_,i)=>i===count-1?[15]:(process.env.TIPY_GALLERY_WALK==='1'?[3,4]:[3]))};
const values=execute(scene,m);
const times=m.frameTimes.slice(1).map((t,i)=>(t-m.frameTimes[i])/15000);
for(let i=0;i<m.frames.length;i+=Math.max(1,Math.floor(count/8)))save('dist/gallery-'+i+'.pgm',Buffer.concat([Buffer.from('P5\\n96 64\\n255\\n'),Buffer.from(m.frames[i].map(p=>p?0:255))]));
console.log(JSON.stringify({fps:1000/(times.reduce((a,b)=>a+b,0)/times.length),bytes:m.bytes,stack:m.stackBytes,frames:m.frames.length,maxMs:Math.max(...times),compiled:m.assembly.includes('__gfx_mesh_vertices_0:'),values}));
`;
writeFileSync(resolve(runner,'dist/gallery-benchmark.mjs'),harness)
const result=spawnSync(process.execPath,['dist/gallery-benchmark.mjs'],{cwd:runner,env:process.env,stdio:'inherit'})
process.exitCode=result.status??1
