const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert=require('node:assert/strict')
const {resolve}=require('node:path')
;(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'})
    try {
        const page=await browser.newPage({viewport:{width:1400,height:810}})
        await page.addInitScript(()=>{
            window.roomFrames=0
            const paint=CanvasRenderingContext2D.prototype.putImageData
            CanvasRenderingContext2D.prototype.putImageData=function(...args){
                const result=paint.apply(this,args)
                if(this.canvas.id==='lcd')window.roomFrames++
                return result
            }
        })
        const errors=[];page.on('pageerror',error=>errors.push(error.message))
        await page.goto('http://127.0.0.1:8084')
        await page.locator('#open').setInputFiles(resolve('../../examples/room.tip'))
        await page.waitForFunction(()=>document.querySelector('#status').textContent==='Opened room.tip')
        await page.getByRole('button',{name:'Set room.tip as entry file',exact:true}).click()
        await page.locator('#run').click()
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
        await page.locator('#execute').click()
        await page.waitForFunction(()=>{
            const c=document.querySelector('#lcd'),p=c.getContext('2d').getImageData(0,0,96,64).data
            return Array.from(p).some((v,i)=>i%4!==3 && v===0)
        })
        const image=()=>page.locator('#lcd').evaluate(c=>c.toDataURL())
        const initial=await image()
        await page.locator('#pause').click()
        await page.locator('#pause').click()
        assert.equal(await page.evaluate(()=>document.activeElement.id),'calculator','Resume must return keyboard focus to the calculator')
        const pressedAt=Date.now()
        await page.keyboard.down('ArrowRight')
        await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,initial)
        const firstResponseMs=Date.now()-pressedAt
        assert.ok(firstResponseMs<1000,'Held input must start moving promptly')
        for(let i=0;i<2;i++) {
            const previous=await image()
            await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,previous)
        }
        await page.keyboard.down('ArrowUp')
        const turning=await image()
        await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,turning)
        await page.keyboard.up('ArrowRight');await page.keyboard.up('ArrowUp')
        assert.equal(await page.locator('#keypad button.pressed').count(),0)
        await page.waitForTimeout(1000)
        const stopped=await image()
        const stoppedFrames=await page.evaluate(()=>window.roomFrames)
        await page.waitForTimeout(500)
        assert.equal(await image(),stopped,'Released keys must stop camera movement')
        assert.equal(await page.evaluate(()=>window.roomFrames),stoppedFrames,'Idle views must not redraw')
        assert.notEqual(stopped,initial)
        await page.locator('#source').click()
        await page.keyboard.press('ArrowRight')
        await page.waitForTimeout(500)
        assert.equal(await image(),stopped,'Editor arrows must navigate text without moving the camera')
        await page.locator('#lcd').click()
        assert.equal(await page.evaluate(()=>document.activeElement.id),'calculator','Clicking the screen must focus input')
        await page.getByRole('button',{name:'Compile',exact:true}).focus()
        await page.keyboard.down('ArrowLeft')
        await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,stopped)
        await page.keyboard.up('ArrowLeft')
        await page.locator('#lcd').click()
        await page.screenshot({path:'dist/room-preview.png',fullPage:true})
        await page.keyboard.down('Escape')
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Finished · '))
        await page.keyboard.up('Escape')
        assert.deepEqual(errors,[])
        console.log(`PASS: room compiles, held arrows turn/pitch, release stops, Escape exits; first response ${firstResponseMs} ms`)
    } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
