import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Ti84Runner, TiLcd, TI } from './hardware.js'
import { EXAMPLES } from './examples.js'
import { compilePyTie, DEFAULT_FILES } from './compiler.js'
import { loadProject } from './project.js'

function finish(runner) {
    for (let i = 0; i < 200 && !runner.done; i++) runner.runSlice()
    assert.equal(runner.done,true,'Program should return or halt')
}

test('CPU speed port selects 6/15 MHz and preserves real-time interrupt timing',()=>{
    const runner=new Ti84Runner('        org 0x9d95\n        ret')
    assert.equal(runner.clockHz,6_000_000);assert.equal(runner.readPort(0x20),0)
    const initial=runner.nextInterrupt
    runner.writePort(0x20,1)
    assert.equal(runner.clockHz,15_000_000);assert.equal(runner.readPort(0x20),1)
    assert.equal(runner.nextInterrupt,initial*2.5)
    runner.writePort(0x20,0)
    assert.equal(runner.clockHz,6_000_000)
    assert.ok(Math.abs(runner.nextInterrupt-initial)<0.0001)
})

test('Machine byte count includes instructions, data and reserved storage', () => {
    const runner = new Ti84Runner('    org 0x9d95\n    ld hl,7\n    ret\n    dw 0\n    defs 8192,0')
    assert.equal(runner.machineBytes, 3 + 1 + 2 + 8192)
    assert.equal(runner.codeDataBytes, runner.machineBytes)
    assert.equal(runner.heapBytes, 0)
})

test('Byte breakdown separates the emitted TiPy heap from code and globals', () => {
    const runner = new Ti84Runner('    org 0x9d95\n    ret\nvalue: dw 0\n__heap_start:\n    defs 8192,0\n__heap_end:')
    assert.equal(runner.machineBytes, 8195)
    assert.equal(runner.codeDataBytes, 3)
    assert.equal(runner.heapBytes, 8192)
})

test('Saved projects migrate legacy extensions and preserve a selected entry file',() => {
    assert.deepEqual(loadProject({ 'main.pti': 'int x=1', 'utils.pti': 'int y=2' }),{
        files: { 'main.tip': 'int x=1', 'utils.tip': 'int y=2' }, entry: 'main.tip'
    })
    assert.deepEqual(loadProject({ files: { 'other.tip': 'pixel(1,1)', 'main.tip': '' }, entry: 'other.tip' }).entry,'other.tip')
    assert.equal(loadProject({ 'main.pti': 'old', 'main.tip': 'new' }).files['main.tip'],'new')
    assert.equal(loadProject({ files: { 'other.tip': 'code' }, entry: 'missing.tip' }).entry,'other.tip')
})
test('RAM graphics do not appear until the actual Z80 program invokes GrBufCpy',() => {
    const runner = new Ti84Runner(`        org 0x9d95
        ld a,0x81
        ld (0x9340),a
        nop
        rst 0x28
        dw 0x486a
        ret`)
    runner.runSlice(1,true)
    runner.runSlice(1,true)
    assert.equal(runner.machine.memory[TI.graph],0x81)
    assert.equal(runner.lcd.pixels.some(Boolean),false)
    finish(runner)
    assert.equal(runner.lcd.pixels[0],1)
    assert.equal(runner.lcd.pixels[7],1)
    assert.equal(runner.lcd.pixels[1],0)
})
test('Z80 keypad polling reads selected groups active low and supports multiple held keys',() => {
    const runner = new Ti84Runner(`        org 0x9d95
        ld a,0xfe
        out (1),a
        in a,(1)
        ld (0x9000),a
        ret`)
    runner.setKey(1,true)
    runner.setKey(3,true)
    runner.setKey(34,true)
    finish(runner)
    assert.equal(runner.machine.memory[0x9000],0xfa)
    runner.writePort(1,0xef)
    assert.equal(runner.readPort(1),0xfd)
    runner.releaseKeys()
    assert.equal(runner.readPort(1),0xff)
})
test('Direct LCD-port output changes LCD but does not touch graphics RAM',() => {
    const runner = new Ti84Runner(EXAMPLES['Direct LCD ports'])
    finish(runner)
    assert.equal(runner.machine.memory.subarray(TI.graph,TI.graph + 768).some(Boolean),false)
    assert.equal(runner.lcd.pixels[0],1)
    assert.equal(runner.lcd.pixels[1],0)
    assert.equal(runner.lcd.pixels[8 * 96],0)
    assert.equal(runner.lcd.pixels[8 * 96 + 1],1)
})
test('Move example executes, displays its graphics buffer and responds to held arrow input',() => {
    const runner = new Ti84Runner(EXAMPLES['Move a pixel'])
    for (let i = 0; i < 8; i++) runner.runSlice()
    const pixel = runner.lcd.pixels.findIndex(Boolean)
    assert.equal(pixel,32 * 96 + 48)
    runner.setKey(3,true)
    for (let i = 0; i < 20; i++) runner.runSlice()
    const moved = runner.lcd.pixels.findIndex(Boolean)
    assert.ok(moved > pixel,`Expected right movement from ${pixel}, got ${moved}`)
    assert.equal(Math.floor(moved / 96),32)
    runner.releaseKeys()
    for (let i = 0; i < 10; i++) runner.runSlice()
    const released = runner.lcd.pixels.findIndex(Boolean)
    for (let i = 0; i < 10; i++) runner.runSlice()
    assert.equal(runner.lcd.pixels.findIndex(Boolean),released)
})
test('Text calls execute from Z80 and draw into LCD, not the graphics buffer',() => {
    const runner = new Ti84Runner(EXAMPLES['Hello screen'])
    finish(runner)
    assert.ok(runner.lcd.pixels.some(Boolean))
    assert.equal(runner.machine.memory[TI.curRow],2)
    assert.equal(runner.machine.memory[TI.curCol],13)
    assert.equal(runner.machine.memory.subarray(TI.graph,TI.graph + 768).some(Boolean),false)
})
test('Unknown OS calls and hardware ports stop explicitly',() => {
    const runner = new Ti84Runner('        org 0x9d95\n        rst 0x28\n        dw 0x4999\n        ret')
    assert.throws(() => finish(runner),/Unsupported TI OS call 0x4999/)
    const io = new Ti84Runner('        org 0x9d95\n        in a,(0x77)\n        ret')
    assert.throws(() => finish(io),/Unsupported TI port read 0x77/)
})
test('LCD six-bit addressing, decrement, on/off and read latch',() => {
    const lcd = new TiLcd()
    lcd.command(0)
    lcd.command(7)
    lcd.command(0x80)
    lcd.command(0x20)
    lcd.write(0x21)
    lcd.write(0x12)
    assert.deepEqual([...lcd.pixels.slice(0,12)],[1,0,0,0,0,1,0,1,0,0,1,0])
    lcd.command(0x20)
    lcd.read()
    assert.equal(lcd.read(),0x21)
    assert.equal(lcd.read(),0x12)
    lcd.command(2)
    assert.equal(lcd.on,false)
    lcd.command(3)
    assert.equal(lcd.on,true)
})
test('Source diagnostics and invalid placement fail before execution',() => {
    assert.throws(() => new Ti84Runner('        org 0x9d95\n        ld potato,hat'),/Line/)
    assert.throws(() => new Ti84Runner('        org 0\n        nop'),/Place code and data in RAM/)
    assert.throws(() => new Ti84Runner(''),/no executable code/)
})

test('PyTie compiles to runnable Z80 and arrow input moves the actual LCD pixel',() => {
    const assembly = compilePyTie(DEFAULT_FILES)
    assert.match(assembly,/org userMem/)
    assert.match(assembly,/out \(1\),a/)
    const runner = new Ti84Runner(assembly)
    for (let i = 0; i < 12; i++) runner.runSlice()
    assert.equal(runner.lcd.pixels.findIndex(Boolean),32 * 96 + 48)
    runner.setKey(3,true)
    for (let i = 0; i < 20; i++) runner.runSlice()
    assert.ok(runner.lcd.pixels.findIndex(Boolean) > 32 * 96 + 48)
})
test('PyTie variables, imported code, signed comparisons, arithmetic and branches execute',() => {
    const runner = new Ti84Runner(compilePyTie({
        'main.tip': `import utils
int x = -12
int total = 0
while total < 3:
    total += 1
if x < 0:
    pixel(total, 4)
else:
    pixel(0, 0)
if x >= -12:
    pixel(8, 4)
if x <= -12:
    pixel(9, 4)
if total > 2:
    pixel(10, 4)
if total == 3:
    pixel(11, 4)
if total != 4:
    pixel(12, 4)
present()`,
        'utils.tip': 'u8 brightness = 255\nbool ready = True'
    }))
    finish(runner)
    assert.equal(runner.lcd.pixels[4 * 96 + 3],1)
    for (let x = 8; x <= 12; x++) assert.equal(runner.lcd.pixels[4 * 96 + x],1)
    assert.equal(runner.lcd.pixels[0],0)
})
test('PyTie rejects untyped declarations, bad types, out-of-range constants and unknown syntax',() => {
    for (const [source,message] of [
        ['u8 brightness = 300',/outside u8/],
        ['int score = 32768',/outside i16/],
        ['bool running = 1',/Expected bool/],
        ['score = 3',/Unknown variable/],
        ['int x = x',/Unknown variable/],
        ['int x = 1;',/Unsupported expression/],
        ['def main():',/Unsupported statement/],
        ['while True:',/Expected an indented block/]
    ]) assert.throws(() => compilePyTie({ 'main.tip': source }),message)
    assert.throws(() => compilePyTie({ 'main.tip': 'import utils', 'utils.tip': 'import main' }),/Circular import/)
})
test('PyTie clips negative and wide coordinates rather than truncating them',() => {
    const runner = new Ti84Runner(compilePyTie({ 'main.tip': 'pixel(-1, 0)\npixel(256, 0)\npixel(0, 256)\npixel(0, -1)\npixel(95, 63)\npresent()' }))
    finish(runner)
    assert.equal(runner.lcd.pixels.reduce((a,b) => a + b,0),1)
    assert.equal(runner.lcd.pixels[6143],1)
})
