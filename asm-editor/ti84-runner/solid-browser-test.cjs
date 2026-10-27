const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert=require('node:assert/strict')
const {resolve}=require('node:path')
;(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'})
    try {
        const page=await browser.newPage({viewport:{width:1400,height:810}})
        const errors=[];page.on('pageerror',e=>errors.push(e.message))
        await page.goto('http://127.0.0.1:8084')
        const open=async name=>{await page.locator('#open').setInputFiles(resolve('../../examples/'+name));await page.waitForFunction(name=>document.querySelector('#status').textContent===`Opened ${name}`,name)}
        const image=()=>page.locator('#lcd').evaluate(c=>c.toDataURL())
        const press=async key=>{
            const previous=await image();await page.keyboard.down(key)
            await page.waitForFunction(previous=>document.querySelector('#lcd').toDataURL()!==previous,previous)
            await page.keyboard.up(key);await page.waitForTimeout(1200)
        }
        const run=async name=>{
            await page.getByRole('button',{name:`Set ${name} as entry file`,exact:true}).click()
            await page.locator('#run').click();await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
            console.log(name,await page.locator('#status').textContent())
            await page.locator('#execute').click();await page.waitForTimeout(1800)
        }
        const stop=async()=>{await page.keyboard.down('Escape');await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Finished · '));await page.keyboard.up('Escape')}
        await open('solid_cube.tip');await run('solid_cube.tip')
        await page.locator('#lcd').screenshot({path:'dist/solid-cube.png'})
        await press('ArrowRight')
        const mode=await page.getByRole('button',{name:'Calculator MODE',exact:true}).boundingBox()
        await page.mouse.move(mode.x+mode.width/2,mode.y+mode.height/2);await page.mouse.down()
        await page.waitForTimeout(1500)
        await page.mouse.up()
        await page.waitForTimeout(300)
        await page.locator('#lcd').screenshot({path:'dist/solid-light.png'})
        await press('Control');await page.locator('#lcd').screenshot({path:'dist/solid-wire.png'});await stop()
        await open('chair.obj')
        assert.equal(await page.getByRole('button',{name:'chair.obj model asset',exact:true}).isDisabled(),true)
        await open('shaded_mesh.tip');await run('shaded_mesh.tip')
        await page.locator('#lcd').screenshot({path:'dist/solid-chair.png'})
        await page.screenshot({path:'dist/solid-browser.png',fullPage:true})
        const chair=await image();await press('ArrowRight');assert.notEqual(await image(),chair)
        await stop();await page.reload()
        assert.equal(await page.getByRole('button',{name:'chair.obj',exact:true}).count()>=1,true)
        assert.deepEqual(errors,[])
        console.log('PASS: solid drawing, OBJ assets, persistence, rotation, light controls, wireframe and exit')
    } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
