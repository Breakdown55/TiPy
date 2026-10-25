import { Ti84Runner, KEYS, HOST_KEYS } from './hardware.js'
import { loadProject } from './project.js'

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
let opened = [...new Set([entry,...['main.tip','utils.tip'].filter(name => Object.hasOwn(files,name))])]
let current = entry
let lastSource = entry
let compileGeneration = 0
let compileRequest = null
let compiling = false
const status = (message,error = false) => { $('status').textContent = message; $('status').classList.toggle('error',error) }
function persist() { try { localStorage.setItem('tipy-project',JSON.stringify({ files,entry })) } catch {} }
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
    if (name !== 'main.asm' && !Object.hasOwn(files,name)) return
    current = name
    if (name !== 'main.asm') lastSource = name
    if (!opened.includes(name)) opened.push(name)
    $('source').value = name === 'main.asm' ? assembly ?? '' : files[name]
    $('source').readOnly = name === 'main.asm'
    $('source').setAttribute('aria-label',name === 'main.asm' ? 'Generated Z80 assembly' : `${name} source code`)
    renderFiles()
}
function renderFiles() {
    $('files').replaceChildren()
    for (const name of Object.keys(files)) {
        const row = document.createElement('div')
        row.className = 'file-row'
        const button = document.createElement('button')
        button.className = 'file' + (name === current ? ' selected' : '')
        button.textContent = name
        button.onclick = () => select(name)
        const entryButton = document.createElement('button')
        entryButton.className = 'entry-toggle' + (name === entry ? ' is-entry' : '')
        entryButton.textContent = name === entry ? '●' : '○'
        entryButton.title = name === entry ? 'Entry file' : 'Set as entry file'
        entryButton.setAttribute('aria-label',`Set ${name} as entry file`)
        entryButton.setAttribute('aria-pressed',String(name === entry))
        entryButton.onclick = () => { save(); invalidateCompile(); entry = name; persist(); renderFiles(); status(`Entry: ${name}`) }
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
        button.setAttribute('aria-pressed',String(name === current))
        const close = document.createElement('button')
        close.className = 'close'
        close.textContent = '×'
        close.setAttribute('aria-label',`Close ${name}`)
        close.onclick = () => {
            save()
            opened = opened.filter(file => file !== name)
            if (!opened.length) opened.push(entry)
            if (current === name) select(opened[0])
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
    $('run').disabled = compiling
    $('run').textContent = compiling ? 'Compiling…' : 'Compile'
    $('execute').disabled = compiling || !runner || running
}
function sizeStatus(prefix) {
    const bytes = value => value.toLocaleString('en-US')
    status(`${prefix} · ${bytes(runner.machineBytes)} bytes (${bytes(runner.codeDataBytes)} code/data + ${bytes(runner.heapBytes)} heap)`)
}
function stop() { running = false; releaseAll(); updateControls() }
async function build() {
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
        paint()
        updateControls()
    } catch (error) { stop(); status(error.message,true) }
}
$('pause').onclick = () => { if (!runner) return; running = !running; if (!running) releaseAll(); lastTime = 0; status(running ? 'Running' : 'Paused'); updateControls() }
$('source').oninput = save
$('source').onkeydown = event => {
    if (event.key === 'Tab' && !$('source').readOnly) { event.preventDefault(); $('source').setRangeText('    ',$('source').selectionStart,$('source').selectionEnd,'end'); save() }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); build() }
}
$('new-file').onclick = () => {
    const name = prompt('File name','utils.tip')?.trim()
    if (!name) return
    if (!/^[A-Za-z_]\w*\.(tip|asm|z80)$/.test(name) || name === 'main.asm') { status('Use a name such as utils.tip. main.asm is generated.',true); return }
    save()
    if (!Object.hasOwn(files,name)) files[name] = ''
    select(name)
    persist()
}
$('open').onchange = async event => {
    const file = event.target.files[0]
    if (!file) return
    if (file.size > 1_000_000) { status('File must be smaller than 1 MB.',true); return }
    const name = file.name === 'main.asm' ? 'input.asm' : file.name
    if (!/^[A-Za-z_]\w*\.(tip|asm|z80)$/.test(name)) { status('Use a file name such as main.tip or input.asm.',true); return }
    save()
    const content = await file.text()
    invalidateCompile()
    files[name] = content
    // Avoid saving the old editor value over a replacement of the active file.
    if (current === name) $('source').value = content
    select(name)
    persist()
    status(`Opened ${name}`)
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
$('calculator').addEventListener('keydown',event => {
    if (event.target.tagName === 'BUTTON' && (event.key === ' ' || event.key === 'Enter')) return
    const code = HOST_KEYS[event.key]
    if (code === undefined) return
    event.preventDefault()
    input(`keyboard${event.code}`,code,true)
})
window.addEventListener('keyup',event => { if (inputs.has(`keyboard${event.code}`)) input(`keyboard${event.code}`,0,false) })
$('calculator').addEventListener('focusout',event => { if (!$('calculator').contains(event.relatedTarget)) releaseAll() })
window.addEventListener('blur',() => { releaseAll(); if (running) { stop(); status('Paused') } })
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
            runner.runSlice(Math.floor(elapsed * 6000))
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
$('source').value = files[entry]
select(entry)
persist()
paintBlank()
requestAnimationFrame(frame)
