const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const { resolve } = require('node:path')
const assert = require('node:assert/strict')

;(async()=>{
    const browser=await chromium.launch({headless:true,channel:'msedge'})
    try {
        const page=await browser.newPage({viewport:{width:1400,height:810}})
        const errors=[]
        page.on('pageerror',error=>errors.push(error.message))
        const file=name=>page.locator('#files .file').filter({hasText:name})
        const saved=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('tipy-project')))
        const current=readFileSync(resolve('../../examples/lumen_gallery.obj'),'utf8').replace(/\r\n/g,'\n')
        await page.goto('http://127.0.0.1:8084')
        await file('chair.obj').waitFor()
        assert.equal(await file('lumen_gallery.obj').count(),1)
        assert.equal((await saved()).entry,'main.tip')
        await page.locator('#open').setInputFiles(resolve('../../examples/lumen_gallery.tip'))
        await page.waitForFunction(()=>document.querySelector('#status').textContent==='Opened lumen_gallery.tip')
        await file('lumen_gallery.tip').click({button:'right'})
        await page.getByRole('menuitem',{name:'Set as entry',exact:true}).click()
        await page.locator('#run').click()
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
        console.log('Source-only import:',await page.locator('#status').textContent())

        const previous=current.replace('# All render objects and colliders use world coordinates.','# Named render objects use exact world/local units.')
            .replace('v -11 -8 0\nv -5 -8 0\nv -5 -2 0\nv -11 -2 0','v -3 0 0\nv 3 0 0\nv 3 6 0\nv -3 6 0')
            .replace('v 12 -8 -10\nv 16 -8 -10\nv 16 -8 -6\nv 12 -8 -6\nv 14 -3 -8','v -2 0 -2\nv 2 0 -2\nv 2 0 2\nv -2 0 2\nv 0 5 0')
        const files={
            'lumen_gallery.tip':readFileSync(resolve('../../examples/lumen_gallery.tip'),'utf8'),
            'lumen_gallery.obj':previous,
            'chair.obj':'# custom chair',
            'personal.tip':'# keep my code'
        }
        await page.evaluate(files=>localStorage.setItem('tipy-project',JSON.stringify({files,entry:'lumen_gallery.tip'})),files)
        await page.reload()
        await file('lumen_gallery.obj').waitFor()
        const migrated=await saved()
        assert.equal(migrated.files['lumen_gallery.obj'].replace(/\r\n/g,'\n'),current)
        assert.equal(migrated.files['chair.obj'],'# custom chair')
        assert.equal(migrated.files['personal.tip'],'# keep my code')
        await page.locator('#run').click()
        await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Compiled · '))
        await file('chair.obj').click({button:'right'})
        await page.getByRole('menuitem',{name:'Delete',exact:true}).click()
        await page.reload()
        await file('lumen_gallery.obj').waitFor()
        assert.equal(await file('chair.obj').count(),0)
        await page.locator('#undo-delete').click()
        assert.equal(await page.locator('#source').inputValue(),'# custom chair')
        assert.deepEqual(errors,[])
        console.log('PASS: built-in OBJ explorer files, source-only gallery compile, exact old-sample update, preserved edits and delete/undo')
    } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1})
