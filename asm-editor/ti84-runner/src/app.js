import { Ti84Runner, KEYS, HOST_KEYS } from './hardware.js'
import { loadProject, removeFile, restoreFile } from './project.js'

const $ = id => document.getElementById(id)
const ctx = $('lcd').getContext('2d')
const image = ctx.createImageData(96,64)
const buttons = new Map()
const inputs = new Map()
let runner = null
let running = false
let started = false
let lastTime = 0
let project = loadProject(null)
let files = project.files
let entry = project.entry
let assembly = null
try {
    project = loadProject(JSON.parse(localStorage.getItem('tipy-project') ?? localStorage.getItem('pytie-project')))
    files = project.files
    entry = project.entry
} catch {}
let deletedFile = project.deletedFile ?? null
let opened = [...new Set([entry,...['main.tip','utils.tip'].filter(name => Object.hasOwn(files,name))].filter(Boolean))]
let current = entry ?? Object.keys(files)[0] ?? null
let lastSource = current
let menuFile = null
let menuTrigger = null
let compileGeneration = 0
let compileRequest = null
let compiling = false
const status = (message,error = false) => { $('status').textContent = message; $('status').classList.toggle('error',error) }
function persist() {
    $('undo-delete').hidden = !deletedFile || Object.hasOwn(files,deletedFile.name)
    if (deletedFile) $('undo-delete').title = `Restore ${deletedFile.name}`
    try { localStorage.setItem('tipy-project',JSON.stringify({ files,entry,deletedFile })) } catch {}
}
function closeFileMenu(restoreFocus = false) {
    $('file-menu').hidden = true
    if (restoreFocus && menuTrigger?.isConnected) menuTrigger.focus()
    menuFile = null
    menuTrigger = null
}
function showFileMenu(event, name, trigger) {
    event.preventDefault()
    event.stopPropagation()
    menuFile = name
    menuTrigger = trigger
    const menu = $('file-menu')
    menu.setAttribute('aria-label',`${name} file actions`)
    $('file-menu-entry').disabled = name === entry || !/\.(tip|asm|z80)$/i.test(name)
    menu.hidden = false
    const rect = trigger.getBoundingClientRect()
    const pointer = Number.isFinite(event.clientX) && Number.isFinite(event.clientY) && (event.clientX !== 0 || event.clientY !== 0)
    const x = pointer ? event.clientX : rect.left
    const y = pointer ? event.clientY : rect.bottom
    menu.style.left = `${Math.max(4,Math.min(x,innerWidth-menu.offsetWidth-4))}px`
    menu.style.top = `${Math.max(4,Math.min(y,innerHeight-menu.offsetHeight-4))}px`
    menu.querySelector('button:not(:disabled)').focus({preventScroll:true})
}
function fileActions(element, name, trigger = element) {
    element.oncontextmenu = event => showFileMenu(event,name,trigger)
    trigger.setAttribute('aria-haspopup','menu')
    trigger.addEventListener('keydown',event => {
        if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') showFileMenu(event,name,trigger)
    })
}
function setEntry(name) {
    if (!Object.hasOwn(files,name) || !/\.(tip|asm|z80)$/i.test(name) || name === entry) return
    save()
    invalidateCompile()
    entry = name
    persist()
    renderFiles()
    status(`Entry: ${name}`)
}
function deleteFile(name) {
    if (!Object.hasOwn(files,name)) return
    save()
    const next = removeFile({ files,entry },name)
    files = next.files
    entry = next.entry
    deletedFile = next.deletedFile
    invalidateCompile()
    assembly = null
    opened = opened.filter(file => file !== name && file !== 'main.asm')
    if (lastSource === name) lastSource = entry ?? Object.keys(files)[0] ?? null
    select(current === name || current === 'main.asm' ? opened[0] ?? entry ?? Object.keys(files)[0] ?? null : current)
    persist()
    paintBlank()
    status(`Deleted ${name}`)
    $('new-file').focus()
}
$('file-menu-entry').onclick = () => setEntry(menuFile)
$('file-menu-delete').onclick = () => deleteFile(menuFile)
$('file-menu').oncontextmenu = event => event.preventDefault()
$('undo-delete').onclick = () => {
    if (!deletedFile || Object.hasOwn(files,deletedFile.name)) return
    save()
    const name = deletedFile.name
    const next = restoreFile({ files,entry,deletedFile })
    files = next.files
    entry = next.entry
    deletedFile = null
    invalidateCompile()
    select(name)
    persist()
    status(`Restored ${name}`)
}
document.addEventListener('pointerdown',event => { if (!$('file-menu').contains(event.target)) closeFileMenu() })
document.addEventListener('contextmenu',() => closeFileMenu())
document.addEventListener('keydown',event => {
    if ($('file-menu').hidden) return
    event.stopImmediatePropagation()
    if (event.key === 'Escape') { event.preventDefault(); closeFileMenu(true); return }
    if (event.key === 'Tab') { closeFileMenu(true); return }
    const items = [...$('file-menu').querySelectorAll('button:not(:disabled)')]
    const index = items.indexOf(document.activeElement)
    let next
    if (event.key === 'ArrowDown') next = (index+1)%items.length
    if (event.key === 'ArrowUp') next = (index+items.length-1)%items.length
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = items.length-1
    if (next !== undefined) { event.preventDefault(); items[next].focus() }
})
window.addEventListener('resize',() => closeFileMenu())
document.addEventListener('scroll',() => closeFileMenu(),true)
function invalidateCompile() {
    compileGeneration++
    compileRequest?.abort()
    compileRequest = null
    const hadProgram = Boolean(runner)
    running = false
    started = false
    releaseAll()
    runner = null
    if (compiling || hadProgram) status('Source changed. Compile again.')
    compiling = false
    updateControls()
}
function save() {
    if (current !== 'main.asm' && Object.hasOwn(files,current) && files[current] !== $('source').value) {
        files[current] = $('source').value
        invalidateCompile()
        persist()
    }
}
function select(name) {
    save()
    if (name !== null && name !== 'main.asm' && !Object.hasOwn(files,name)) return
    current = name
    if (name && name !== 'main.asm') lastSource = name
    if (name && !opened.includes(name)) opened.push(name)
    $('source').value = name === 'main.asm' ? assembly ?? '' : files[name] ?? ''
    $('source').readOnly = name === null || name === 'main.asm'
    $('source').placeholder = name === null ? 'Open or create a file' : ''
    $('source').setAttribute('aria-label',name === null ? 'Source code' : name === 'main.asm' ? 'Generated Z80 assembly' : `${name} source code`)
    renderFiles()
}
function renderFiles() {
    closeFileMenu()
    $('files').replaceChildren()
    for (const name of Object.keys(files)) {
        const row = document.createElement('div')
        row.className = 'file-row'
        const button = document.createElement('button')
        button.className = 'file' + (name === current ? ' selected' : '')
        button.textContent = name
        button.onclick = () => select(name)
        fileActions(row,name,button)
        const entryButton = document.createElement('button')
        entryButton.className = 'entry-toggle' + (name === entry ? ' is-entry' : '')
        entryButton.textContent = name === entry ? '●' : '○'
        entryButton.title = name === entry ? 'Entry file' : 'Set as entry file'
        entryButton.setAttribute('aria-label',`Set ${name} as entry file`)
        entryButton.setAttribute('aria-pressed',String(name === entry))
        if(name.endsWith('.obj')){entryButton.disabled=true;entryButton.title='OBJ model asset';entryButton.setAttribute('aria-label',`${name} model asset`)}
        entryButton.onclick = () => setEntry(name)
        row.append(button,entryButton)
        $('files').append(row)
    }
    $('tabs').replaceChildren()
    for (const name of opened) {
        const tab = document.createElement('div')
        tab.className = 'tab' + (name === current ? ' active' : '')
        const button = document.createElement('button')
        button.textContent = name === 'main.asm' ? 'Z80 assembly' : name
        button.onclick = () => select(name)
        if (name !== 'main.asm') fileActions(tab,name,button)
        button.setAttribute('aria-pressed',String(name === current))
        const close = document.createElement('button')
        close.className = 'close'
        close.textContent = '×'
        close.setAttribute('aria-label',`Close ${name}`)
        close.onclick = () => {
            save()
            opened = opened.filter(file => file !== name)
            if (!opened.length && (entry || Object.keys(files)[0])) opened.push(entry ?? Object.keys(files)[0])
            if (current === name) select(opened[0] ?? null)
            else renderFiles()
        }
        tab.append(button)
        if (name === entry) {
            const badge = document.createElement('span')
            badge.className = 'entry-badge'
            badge.textContent = 'ENTRY'
            tab.append(badge)
        }
        tab.append(close)
        $('tabs').append(tab)
    }
}
function updateControls() {
    $('pause').hidden = !started || !runner || runner.done
    $('pause').textContent = running ? 'Pause' : 'Resume'
    $('run').disabled = compiling || !entry
    $('run').textContent = compiling ? 'Compiling…' : 'Compile'
    $('execute').disabled = compiling || !runner || running
}
function sizeStatus(prefix) {
    const bytes = value => value.toLocaleString('en-US')
    status(`${prefix} · ${bytes(runner.machineBytes)} bytes (${bytes(runner.codeDataBytes)} code/data + ${bytes(runner.heapBytes)} heap)`)
}
function stop() { running = false; releaseAll(); updateControls() }
async function build() {
    if (!entry) return
    save()
    invalidateCompile()
    const generation = compileGeneration
    stop()
    runner = null
    assembly = null
    if (current !== 'main.asm') opened = opened.filter(name => name !== 'main.asm')
    renderFiles()
    paintBlank()
    updateControls()
    try {
        let generated
        if (/\.(asm|z80)$/i.test(entry)) generated = files[entry]
        else {
            compiling = true
            compileRequest = new AbortController()
            updateControls()
            status('Compiling…')
            const response = await fetch('/api/compile',{
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files,entry }), signal: compileRequest.signal
            })
            let result
            try { result = await response.json() }
            catch { throw new Error('Python compiler service is unavailable. Restart the local server.') }
            if (generation !== compileGeneration) return
            if (!response.ok || !result.ok) {
                const error = result.error ?? { message: 'Compilation failed' }
                const location = error.filename ? `${error.filename}:${error.line}:${error.column}: ` : ''
                throw new Error(location + error.message)
            }
            if (result.compiler !== 'python' || typeof result.assembly !== 'string') throw new Error('Expected assembly from the Python compiler')
            generated = result.assembly
        }
        if (generation !== compileGeneration) return
        runner = new Ti84Runner(generated)
        assembly = generated
        if (!opened.includes('main.asm')) opened.push('main.asm')
        if (current === 'main.asm') $('source').value = assembly
        renderFiles()
        running = false
        started = false
        lastTime = 0
        sizeStatus('Compiled')
        paint()
    } catch (error) {
        if (generation === compileGeneration && error.name !== 'AbortError') {
            if (current === 'main.asm') select(lastSource)
            opened = opened.filter(name => name !== 'main.asm')
            renderFiles()
            status(error.message,true)
        }
    } finally {
        if (generation === compileGeneration) { compiling = false; compileRequest = null; updateControls() }
    }
}
$('run').onclick = build
$('execute').onclick = () => {
    if (!runner || !assembly || compiling) return
    try {
        stop()
        runner = new Ti84Runner(assembly)
        started = true
        running = true
        lastTime = 0
        status('Running')
        $('calculator').focus()
        paint()
        updateControls()
    } catch (error) { stop(); status(error.message,true) }
}
$('pause').onclick = () => {
    if (!runner) return
    running = !running
    if (running) $('calculator').focus({preventScroll:true})
    else releaseAll()
    lastTime = 0
    status(running ? 'Running' : 'Paused')
    updateControls()
}
$('source').oninput = save
$('source').onkeydown = event => {
    if (event.key === 'Tab' && !$('source').readOnly) { event.preventDefault(); $('source').setRangeText('    ',$('source').selectionStart,$('source').selectionEnd,'end'); save() }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); build() }
}
$('new-file').onclick = () => {
    const name = prompt('File name','utils.tip')?.trim()
    if (!name) return
    if (!/^[A-Za-z_]\w*\.(tip|asm|z80|obj)$/.test(name) || name === 'main.asm') { status('Use a .tip, .obj, .asm, or .z80 name. main.asm is generated.',true); return }
    save()
    if (!Object.hasOwn(files,name)) files[name] = ''
    if (!entry && !name.endsWith('.obj')) entry = name
    select(name)
    persist()
    updateControls()
}
$('open').onchange = async event => {
    const selected = Array.from(event.target.files)
    if (!selected.length) return
    for (const file of selected) {
        if (file.size > 1_000_000) { status('Each file must be smaller than 1 MB.',true); return }
        if (!/^[A-Za-z_]\w*\.(tip|asm|z80|obj)$/.test(file.name)) { status('Use .tip, .obj, .asm, or .z80 file names.',true); return }
    }
    const contents = await Promise.all(selected.map(file => file.text()))
    save()
    invalidateCompile()
    const names = selected.map(file => file.name === 'main.asm' ? 'input.asm' : file.name)
    for (let i=0;i<names.length;i++) files[names[i]] = contents[i]
    const sources = names.filter(name => name.endsWith('.tip'))
    const active = sources[0] ?? names[0]
    if (names.length > 1 && sources.length === 1) entry = active
    if (!entry) entry = sources[0] ?? names.find(name => !name.endsWith('.obj')) ?? null
    // Do not overwrite a replacement with the old textarea value during select.
    if (names.includes(current)) $('source').value = files[current]
    select(active)
    persist()
    updateControls()
    status(names.length === 1 ? `Opened ${active}` : `Opened ${names.length} files`)
    event.target.value = ''
}

function input(token,code,down) {
    if (down) inputs.set(token,code)
    else inputs.delete(token)
    const active = new Set(inputs.values())
    for (const [key,button] of buttons) { const held = active.has(key); runner?.setKey(key,held); button.classList.toggle('pressed',held) }
}
function releaseAll() { inputs.clear(); runner?.releaseKeys(); for (const button of buttons.values()) button.classList.remove('pressed') }
for (const [label,code] of KEYS) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = label
    button.dataset.code = String(code)
    button.setAttribute('aria-label',`Calculator ${label}`)
    button.onpointerdown = event => { event.preventDefault(); $('calculator').focus({ preventScroll: true }); button.setPointerCapture(event.pointerId); input(`pointer${event.pointerId}`,code,true) }
    button.onpointerup = event => input(`pointer${event.pointerId}`,code,false)
    button.onpointercancel = event => input(`pointer${event.pointerId}`,code,false)
    button.onlostpointercapture = event => input(`pointer${event.pointerId}`,code,false)
    button.onkeydown = event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); input(`button${code}`,code,true) } }
    button.onkeyup = event => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); input(`button${code}`,code,false) } }
    buttons.set(code,button)
    $('keypad').append(button)
}
$('lcd').addEventListener('pointerdown',()=>$('calculator').focus({preventScroll:true}))
document.addEventListener('keydown',event => {
    const inCalculator=$('calculator').contains(event.target)
    const editing=event.target.matches('input,textarea,select') || event.target.isContentEditable
    // Arrow controls also work after clicking non-editable UI outside the
    // calculator. Keep editor navigation and browser shortcuts intact.
    if (!inCalculator && (!running || editing || !event.key.startsWith('Arrow') || event.ctrlKey || event.altKey || event.metaKey)) return
    if (event.target.tagName === 'BUTTON' && (event.key === ' ' || event.key === 'Enter')) return
    const code = HOST_KEYS[event.key]
    if (code === undefined) return
    event.preventDefault()
    input(`keyboard${event.code}`,code,true)
})
window.addEventListener('keyup',event => { if (inputs.has(`keyboard${event.code}`)) input(`keyboard${event.code}`,0,false) })
$('calculator').addEventListener('focusout',event => { if (!$('calculator').contains(event.relatedTarget)) releaseAll() })
window.addEventListener('blur',() => { closeFileMenu(); releaseAll(); if (running) { stop(); status('Paused') } })
document.addEventListener('visibilitychange',() => { if (document.hidden) stop() })
function paintBlank() { ctx.fillStyle = '#fff'; ctx.fillRect(0,0,96,64) }
function paint() {
    if (!runner?.lcd.dirty) return
    for (let i = 0; i < 6144; i++) {
        const color = runner.lcd.on && runner.lcd.pixels[i] ? 0 : 255
        image.data.set([color,color,color,255],i * 4)
    }
    ctx.putImageData(image,0,0)
    runner.lcd.dirty = false
}
function frame(time) {
    if (running && runner) {
        try {
            const elapsed = lastTime ? Math.min(time - lastTime,40) : 16
            lastTime = time
            runner.runSlice(Math.floor(elapsed * runner.clockHz / 1000))
            if (runner.done) {
                stop()
                sizeStatus('Finished')
            }
        } catch (error) { stop(); status(error.message,true) }
        paint()
    }
    requestAnimationFrame(frame)
}
// Loading the first file must not overwrite it with the initially empty textarea.
$('source').value = files[current] ?? ''
select(current)
persist()
updateControls()
paintBlank()
requestAnimationFrame(frame)
