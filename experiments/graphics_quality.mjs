// Run after `node build.mjs` in asm-editor/ti84-runner. Uses its native test
// harness so every screenshot and timing comes from executed Z80 instructions.
import {readFileSync,writeFileSync} from 'node:fs'
import {resolve,dirname} from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync} from 'node:child_process'

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..')
const runner=resolve(root,'asm-editor/ti84-runner')
const tests=readFileSync(resolve(runner,'dist/python-compiler.test.mjs'),'utf8')
const end=tests.indexOf('test("Perspective camera')
if(end<0)throw Error('Native harness boundary changed')
let harness=tests.slice(0,end)
const argsStart=harness.indexOf('  const args = measurements?.project')
const argsEnd=harness.indexOf('  const processResult =',argsStart)
if(argsStart<0||argsEnd<0)throw Error('Native compiler entry changed')
harness=harness.slice(0,argsStart)+`  const args=[resolve2('../../experiments/graphics_quality.py'),String(measurements.quality)];\n`+harness.slice(argsEnd)
harness+=String.raw`
const {writeFileSync:savePreview}=await import('node:fs');
const game=readFileSync(resolve2('../../examples/lumen_run.tip'),'utf8');
// Fix the angular increment only for this comparison, so faster variants
// render the same camera poses. HUD, drawing, lighting and input stay active.
const source=game.replace('set_render_quality("aggressive")','')
    .replace('int yaw=0','int yaw=3')
    .replace('yaw=(yaw+turn*(turn_remainder//3))&127','yaw=(yaw+turn*2)&127');
const names=['Exact','Conservative math','Balanced','Aggressive','Maximum'];
const results=[];
for(let quality=0;quality<5;quality++){
    if(process.env.TIPY_BENCH_QUALITY!==undefined&&quality!==Number(process.env.TIPY_BENCH_QUALITY))continue;
    const m={project:true,quality,optimization:'aggressive',maxSlices:20000,guardGraph:true,trackStack:true,
      afterFrameKeys:Array.from({length:8},(_,i)=>i===7?[15]:[3])};
    execute(source,m);
    const times=m.frameTimes.slice(1).map((t,i)=>(t-m.frameTimes[i])/15_000);
    const ms=times.reduce((a,b)=>a+b,0)/times.length;
    const pixels=m.frames[2];
    savePreview('dist/quality-'+quality+'.pgm',Buffer.concat([Buffer.from('P5\n96 64\n255\n'),Buffer.from(pixels.map(p=>p?0:255))]));
    results.push({quality,name:names[quality],ms,fps:1000/ms,bytes:m.bytes,stack:m.stackBytes,times,
      difference:quality===0?0:!globalThis.qualityReference?null:pixels.reduce((n,p,i)=>n+(p!==globalThis.qualityReference[i]),0)});
    if(quality===0)globalThis.qualityReference=pixels;
    console.log(JSON.stringify(results.at(-1)));
}
savePreview('dist/quality-results.json',JSON.stringify(results,null,2));
`;
writeFileSync(resolve(runner,'dist/graphics-quality-capture.mjs'),harness)
const result=spawnSync(process.execPath,['dist/graphics-quality-capture.mjs'],{cwd:runner,env:process.env,stdio:'inherit'})
process.exitCode=result.status??1
