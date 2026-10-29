const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { resolve } = require('node:path')
const assert = require('node:assert/strict')

;(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'})
    try {
        const page=await browser.newPage({viewport:{width:1400,height:810}})
        const errors=[]
        page.on('pageerror',error=>errors.push(error.message))
        await page.goto('http://127.0.0.1:8084')
        for(const name of ['toolkit_2d.tip','pipeline_control.tip']){
            await page.locator('#open').setInputFiles(resolve('../../examples',name))
            await page.waitForFunction(name=>document.querySelector('#status').textContent===`Opened ${name}`,name)
            await page.locator('#files .file').filter({hasText:name}).click({button:'right'})
            await page.getByRole('menuitem',{name:'Set as entry',exact:true}).click()
            await page.locator('#run').click()
            await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
            assert.match(await page.locator('#status').textContent(),/\+ 0 heap/)
            assert.equal(await page.locator('#source').getAttribute('aria-label'),`${name} source code`)
            console.log(name,await page.locator('#status').textContent())
            await page.locator('#execute').click()
            if(name==='toolkit_2d.tip'){
                await page.waitForFunction(()=>Array.from(document.querySelector('#lcd').getContext('2d').getImageData(0,0,96,64).data).some((value,i)=>i%4!==3&&value===0))
                await page.locator('#lcd').click()
                const image=await page.locator('#lcd').evaluate(canvas=>canvas.toDataURL())
                await page.keyboard.down('ArrowRight')
                await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,image)
                await page.keyboard.up('ArrowRight')
                await page.screenshot({path:'dist/toolkit-2d-browser.png',fullPage:true})
                await page.keyboard.down('Escape')
                await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Finished · '))
                await page.keyboard.up('Escape')
            }else{
                await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Finished · '))
                const row=await page.locator('#lcd').evaluate(canvas=>Array.from(canvas.getContext('2d').getImageData(0,0,8,1).data).filter((_,i)=>i%4===0))
                assert.deepEqual(row,[0,255,0,255,0,255,0,255])
                await page.screenshot({path:'dist/toolkit-pipeline-browser.png',fullPage:true})
            }
        }
        assert.deepEqual(errors,[])
        console.log('PASS: editable source libraries, pygame-level drawing/input/clock loop, GX view matrix/framebuffer access, native compile/run/exit')
    }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
