const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert=require('node:assert/strict')
const {resolve}=require('node:path')
;(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'})
    try {
        const page=await browser.newPage({viewport:{width:1400,height:810}})
        const errors=[];page.on('pageerror',error=>errors.push(error.message))
        await page.goto('http://127.0.0.1:8084')
        await page.locator('#open').setInputFiles(resolve('../../examples/mono96.tip'))
        await page.waitForFunction(()=>document.querySelector('#status').textContent==='Opened mono96.tip')
        await page.getByRole('button',{name:'Set mono96.tip as entry file',exact:true}).click()
        await page.locator('#run').click()
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
        await page.locator('#execute').click()
        await page.waitForFunction(()=>{
            const p=document.querySelector('#lcd').getContext('2d').getImageData(0,0,96,64).data
            return Array.from(p).some((v,i)=>i%4!==3 && v===0)
        })
        const image=()=>page.locator('#lcd').evaluate(c=>c.toDataURL())
        const hud=()=>page.locator('#lcd').evaluate(c=>Array.from(c.getContext('2d').getImageData(1,56,74,7).data).join(','))
        const next=async()=>{
            const previous=await hud();await page.keyboard.down('ArrowRight')
            await page.waitForFunction(previous=>Array.from(document.querySelector('#lcd').getContext('2d').getImageData(1,56,74,7).data).join(',')!==previous,previous)
            await page.keyboard.up('ArrowRight');await page.waitForTimeout(250)
        }
        const first=await image();await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,first)
        await next();await next()
        await page.keyboard.down('Enter');await page.waitForTimeout(300);await page.keyboard.up('Enter');await page.waitForTimeout(250)
        const paused=await image();await page.waitForTimeout(400);assert.equal(await image(),paused)
        await page.screenshot({path:'dist/mono96-browser.png',fullPage:true})
        await page.keyboard.down('Enter');await page.waitForTimeout(200);await page.keyboard.up('Enter')
        await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,paused)
        await page.keyboard.down('Escape')
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Finished · '))
        await page.keyboard.up('Escape');assert.deepEqual(errors,[])
        console.log('PASS: MONO/96 animates, switches scenes, freezes/resumes, and exits')
    } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
