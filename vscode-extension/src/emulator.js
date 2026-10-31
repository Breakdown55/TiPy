import { Ti84Runner, KEYS, HOST_KEYS } from '../../asm-editor/ti84-runner/src/hardware.js'

const vscode = acquireVsCodeApi()
const canvas = document.querySelector('canvas')
const ctx = canvas.getContext('2d', { alpha: false })
const image = ctx.createImageData(96, 64)
const status = document.querySelector('#status')
const pauseButton = document.querySelector('#pause')
let runner, source, running = false, autoPaused = false, viewActive = true, last = 0, frame = 0
const held = new Map()
function paint() {
    for (let i = 0; i < 6144; i++) {
        const value = runner?.lcd.on && runner.lcd.pixels[i] ? 0 : 255
        image.data.set([value, value, value, 255], i * 4)
    }
    ctx.putImageData(image, 0, 0)
    if (runner) runner.lcd.dirty = false
}
function release() { held.clear(); runner?.releaseKeys() }
function key(token, code, down) {
    if (!runner || !running) return
    const previous = new Set(held.values())
    down ? held.set(token, code) : held.delete(token)
    const current = new Set(held.values())
    for (const value of previous) if (!current.has(value)) runner.setKey(value, false)
    for (const value of current) runner.setKey(value, true)
}
function stop(label = 'Stopped', resumable = false) {
    autoPaused = false
    running = false; cancelAnimationFrame(frame); release(); paint()
    if (!resumable) runner = null
    status.textContent = label; pauseButton.textContent = 'Resume'
    pauseButton.disabled = !runner || runner.done
}
function tick(now) {
    if (!running) return
    try {
        const elapsed = last ? Math.min(now - last, 40) : 16
        last = now
        runner.runSlice(Math.floor(elapsed * runner.clockHz / 1000))
        if (runner.lcd.dirty) paint()
        if (runner.done) { stop('Finished'); vscode.postMessage({ type: 'finished' }); return }
        frame = requestAnimationFrame(tick)
    } catch (error) { stop(error.message); vscode.postMessage({ type: 'error', message: error.message }) }
}
function resume() {
    if (!runner || runner.done || running) return
    autoPaused = false
    canvas.focus()
    running = true; last = 0; status.textContent = 'Running'; pauseButton.textContent = 'Pause'
    pauseButton.disabled = false
    frame = requestAnimationFrame(tick)
}
function pauseAutomatically() {
    if (!running) return
    stop('Paused', true)
    autoPaused = true
}
function resumeAutomatically() {
    if (autoPaused && viewActive && !document.hidden) resume()
}
function run(assembly, filename) {
    stop(); source = { assembly, filename }
    try {
        runner = new Ti84Runner(assembly)
        document.querySelector('#name').textContent = filename
        document.querySelector('#size').textContent = `${runner.machineBytes.toLocaleString()} bytes (${runner.codeDataBytes.toLocaleString()} code/data + ${runner.heapBytes.toLocaleString()} heap)`
        paint(); canvas.focus(); resume()
        vscode.postMessage({ type: 'started', bytes: runner.machineBytes })
    } catch (error) { runner = null; stop(error.message); vscode.postMessage({ type: 'error', message: error.message }) }
}
window.addEventListener('message', ({ data }) => {
    if (data.type === 'run' && typeof data.assembly === 'string') run(data.assembly, data.filename)
    if (data.type === 'stop') stop()
    if (data.type === 'viewState') {
        viewActive = data.active
        if (data.active) resumeAutomatically()
        else pauseAutomatically()
    }
})
document.querySelector('#pause').onclick = () => running ? stop('Paused', true) : resume()
document.querySelector('#restart').onclick = () => { if (source) run(source.assembly, source.filename) }
document.querySelector('#stop').onclick = () => stop()
canvas.addEventListener('keydown', event => {
    const code = HOST_KEYS[event.key]
    if (code === undefined) return
    event.preventDefault(); event.stopPropagation(); key('key:' + event.code, code, true)
})
canvas.addEventListener('keyup', event => {
    const code = HOST_KEYS[event.key]
    if (code === undefined) return
    event.preventDefault(); event.stopPropagation(); key('key:' + event.code, code, false)
})
canvas.addEventListener('blur', release)
window.addEventListener('blur', pauseAutomatically)
window.addEventListener('focus', resumeAutomatically)
document.addEventListener('visibilitychange', () => {
    if (document.hidden) pauseAutomatically()
    else resumeAutomatically()
})
for (const [label, code] of KEYS) {
    const button = document.createElement('button')
    button.textContent = label
    button.onpointerdown = event => { event.preventDefault(); button.setPointerCapture(event.pointerId); canvas.focus(); key('pointer:' + event.pointerId, code, true) }
    button.onpointerup = button.onpointercancel = event => key('pointer:' + event.pointerId, code, false)
    document.querySelector('#keys').append(button)
}
stop('Ready')
vscode.postMessage({ type: 'ready' })
