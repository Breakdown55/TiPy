import { assemble, Z80Machine, StopReason } from '@specy/z80'

export const TI = Object.freeze({ width: 96, height: 64, graph: 0x9340, graphSize: 768, user: 0x9d95, curRow: 0x844b, curCol: 0x844c, penCol: 0x86d7, penRow: 0x86d8, flags: 0x89f0 })
export const KEYS = [
    ['Y=',49], ['WINDOW',50], ['ZOOM',51], ['TRACE',52], ['GRAPH',53],
    ['2nd',54], ['MODE',55], ['DEL',56], ['↑',4], ['ALPHA',48],
    ['X,T,θ,n',40], ['STAT',32], ['←',2], ['↓',1], ['→',3],
    ['MATH',47], ['APPS',39], ['PRGM',31], ['VARS',23], ['CLEAR',15],
    ['x⁻¹',46], ['SIN',38], ['COS',30], ['TAN',22], ['^',14],
    ['x²',45], [',',37], ['(',29], [')',21], ['÷',13],
    ['LOG',44], ['7',36], ['8',28], ['9',20], ['×',12],
    ['LN',43], ['4',35], ['5',27], ['6',19], ['−',11],
    ['STO',42], ['1',34], ['2',26], ['3',18], ['+',10],
    ['ON',0], ['0',33], ['.',25], ['(−)',17], ['ENTER',9]
]
const VALID_KEYS = new Set(KEYS.map(([,code]) => code))
export const HOST_KEYS = { ArrowDown: 1, ArrowLeft: 2, ArrowRight: 3, ArrowUp: 4, Enter: 9, '+': 10, '-': 11, '*': 12, '/': 13, '^': 14, Backspace: 56, Escape: 15, '3': 18, '6': 19, '9': 20, ')': 21, '.': 25, '2': 26, '5': 27, '8': 28, '(': 29, '0': 33, '1': 34, '4': 35, '7': 36, ',': 37, Shift: 48, Control: 54 }

// Host letters correspond to the real keys carrying TI's ALPHA legends.
// Native programs decide whether ALPHA is off, uppercase, or lowercase.
const ALPHA_CODES = [47,39,31,46,38,30,22,14,45,37,29,21,13,44,36,28,20,12,43,35,27,19,11,42,34,26]
for (let i=0;i<26;i++) {
    HOST_KEYS[String.fromCharCode(65+i)] = ALPHA_CODES[i]
    HOST_KEYS[String.fromCharCode(97+i)] = ALPHA_CODES[i]
}
HOST_KEYS[' '] = 33

// LCD RAM is independent of CPU RAM. A RAM store cannot update the display.
export class TiLcd {
    constructor() { this.reset() }
    reset() {
        this.pixels = new Uint8Array(96 * 64)
        this.row = 0
        this.column = 0
        this.word = 8
        this.mode = 5
        this.on = true
        this.contrast = 32
        this.dummy = true
        this.readLatch = 0
        this.dirty = true
    }
    command(value) {
        if (value === 0 || value === 1) this.word = value ? 8 : 6
        else if (value === 2 || value === 3) { this.on = value === 3; this.dirty = true }
        else if (value >= 4 && value <= 7) this.mode = value
        else if ((value & 0xe0) === 0x20) { this.column = value & 31; this.dummy = true }
        else if ((value & 0xc0) === 0x80) { this.row = value & 63; this.dummy = true }
        else if ((value & 0xc0) === 0xc0) this.contrast = value & 63
        else throw new Error(`Unsupported LCD command ${hex(value, 2)}`)
    }
    advance() {
        if (this.mode === 4) this.row = (this.row + 63) % 64
        if (this.mode === 5) this.row = (this.row + 1) % 64
        const columns = this.word === 8 ? 15 : 20
        if (this.mode === 6) this.column = (this.column + columns - 1) % columns
        if (this.mode === 7) this.column = (this.column + 1) % columns
    }
    write(value) {
        for (let bit = 0; bit < this.word; bit++) {
            const x = this.column * this.word + bit
            if (x < 96) this.pixels[this.row * 96 + x] = (value >> (this.word - 1 - bit)) & 1
        }
        this.advance()
        this.dirty = true
    }
    read() {
        const previous = this.readLatch
        // The first read fills the latch without moving. Subsequent reads
        // return the latch, advance the address and fetch the next word.
        if (!this.dummy) this.advance()
        this.readLatch = 0
        for (let bit = 0; bit < this.word; bit++) {
            const x = this.column * this.word + bit
            this.readLatch = (this.readLatch << 1) | (x < 96 ? this.pixels[this.row * 96 + x] : 0)
        }
        this.dummy = false
        return previous
    }
    status() { return (this.word === 8 ? 0x40 : 0) | (this.on ? 0x20 : 0) | (this.mode >= 6 ? 2 : 0) | (this.mode & 1) }
    clear() { this.pixels.fill(0); this.dirty = true }
    copy(memory) {
        for (let i = 0; i < 768; i++) for (let bit = 0; bit < 8; bit++) this.pixels[i * 8 + bit] = (memory[TI.graph + i] >> (7 - bit)) & 1
        this.dirty = true
    }
}

// Original compact ASCII font for the preview; TI's ROM font is not included.
const FONT = {
    A:'01110/10001/10001/11111/10001/10001/10001', B:'11110/10001/10001/11110/10001/10001/11110', C:'01111/10000/10000/10000/10000/10000/01111',
    D:'11110/10001/10001/10001/10001/10001/11110', E:'11111/10000/10000/11110/10000/10000/11111', F:'11111/10000/10000/11110/10000/10000/10000',
    G:'01111/10000/10000/10111/10001/10001/01111', H:'10001/10001/10001/11111/10001/10001/10001', I:'11111/00100/00100/00100/00100/00100/11111',
    J:'00111/00010/00010/00010/10010/10010/01100', K:'10001/10010/10100/11000/10100/10010/10001', L:'10000/10000/10000/10000/10000/10000/11111',
    M:'10001/11011/10101/10101/10001/10001/10001', N:'10001/11001/10101/10011/10001/10001/10001', O:'01110/10001/10001/10001/10001/10001/01110',
    P:'11110/10001/10001/11110/10000/10000/10000', Q:'01110/10001/10001/10001/10101/10010/01101', R:'11110/10001/10001/11110/10100/10010/10001',
    S:'01111/10000/10000/01110/00001/00001/11110', T:'11111/00100/00100/00100/00100/00100/00100', U:'10001/10001/10001/10001/10001/10001/01110',
    V:'10001/10001/10001/10001/10001/01010/00100', W:'10001/10001/10001/10101/10101/10101/01010', X:'10001/10001/01010/00100/01010/10001/10001',
    Y:'10001/10001/01010/00100/00100/00100/00100', Z:'11111/00001/00010/00100/01000/10000/11111',
    '0':'01110/10001/10011/10101/11001/10001/01110', '1':'00100/01100/00100/00100/00100/00100/01110', '2':'01110/10001/00001/00010/00100/01000/11111',
    '3':'11110/00001/00001/01110/00001/00001/11110', '4':'00010/00110/01010/10010/11111/00010/00010', '5':'11111/10000/10000/11110/00001/00001/11110',
    '6':'01110/10000/10000/11110/10001/10001/01110', '7':'11111/00001/00010/00100/01000/01000/01000', '8':'01110/10001/10001/01110/10001/10001/01110',
    '9':'01110/10001/10001/01111/00001/00001/01110', ' ':'00000/00000/00000/00000/00000/00000/00000',
    '-':'00000/00000/00000/11111/00000/00000/00000', '.':'00000/00000/00000/00000/00000/00100/00100', '!':'00100/00100/00100/00100/00100/00000/00100',
    ':':'00000/00100/00100/00000/00100/00100/00000', '?':'01110/10001/00001/00010/00100/00000/00100'
}
const BCALLS = { _GetCSC: 0x4018, _PutMap: 0x4501, _PutC: 0x4504, _DispHL: 0x4507, _PutS: 0x450a, _ClrLCDFull: 0x4540, _ClrScrnFull: 0x4546, _HomeUp: 0x4558, _GrBufCpy: 0x486a, _GrBufClr: 0x4bd0, _MemSet: 0x4c33 }
export const INCLUDE = Object.entries({ ...BCALLS, plotSScreen: TI.graph, userMem: TI.user, curRow: TI.curRow, curCol: TI.curCol, penCol: TI.penCol, penRow: TI.penRow, flags: TI.flags }).map(([key,value]) => `${key} equ ${hex(value)}`).join('\n')

export class Ti84Runner {
    constructor(source) {
        this.lcd = new TiLcd()
        this.keys = new Set()
        this.keyMask = 255
        this.nextInterrupt = 6_000_000 / 118
        this.interruptMask = 0x0b
        this.interruptPending = 0
        this.bank = 0
        this.flash = new Uint8Array(128 * 0x4000)
        this.lastScan = 0
        this.scanTime = 0
        this.done = false
        this.machine = new Z80Machine({ initialSp: 0xfffe, exitOnReturn: true, historySize: 0,
            onPortRead: address => this.readPort(address & 255),
            onPortWrite: (address,value) => this.writePort(address & 255,value),
            onMemoryRead: address => address < 0x8000 ? this.flash[(address < 0x4000 ? 0 : this.bank) * 0x4000 + (address & 0x3fff)] : undefined,
            onDebugRead: address => address < 0x8000 ? this.flash[(address < 0x4000 ? 0 : this.bank) * 0x4000 + (address & 0x3fff)] : undefined,
            onMemoryWrite: address => address < 0x8000
        })
        const result = assemble({ 'main.asm': source, 'ti84.inc': INCLUDE }, { entryPathname: 'main.asm' })
        if (result.hasErrors()) throw new Error(result.diagnostics.map(d => `Line ${(d.lineNumber ?? 0) + 1}: ${d.message}`).join('\n'))
        if (!result.segments().length) throw new Error('The source contains no executable code')
        for (const segment of result.segments()) if (segment.address < 0x8000 || segment.address + segment.bytes.length > 0xfffe) throw new Error(`Place code and data in RAM, between 0x8000 and 0xFFFD (${segment.bytes.length.toLocaleString('en-US')} bytes at 0x${segment.address.toString(16).toUpperCase()})`)
        this.machine.loadAssembly(result)
        // RST 28 is intercepted at the CPU boundary; IM1 returns through EI / RETI.
        this.flash.set([0xfb, 0xed, 0x4d], 0x38)
        this.machine.z80.regs.iy = TI.flags
        this.machine.z80.regs.im = 1
        this.machine.z80.regs.iff1 = 1
        this.machine.z80.regs.iff2 = 1
        this.assembly = result
        this.machineBytes = result.segments().reduce((total, segment) => total + segment.bytes.length, 0)
        const symbols = result.asm.scopes[0].symbols
        const heapStart = symbols.get('__heap_start')?.value
        const heapEnd = symbols.get('__heap_end')?.value
        this.heapBytes = Number.isInteger(heapStart) && Number.isInteger(heapEnd) && heapEnd >= heapStart
            ? result.segments().reduce((total, segment) => total + Math.max(0,
                Math.min(heapEnd, segment.address + segment.bytes.length) - Math.max(heapStart, segment.address)), 0)
            : 0
        this.codeDataBytes = this.machineBytes - this.heapBytes
        this.listErrorAddress = result.asm.scopes[0].symbols.get('__list_error')?.value
    }
    setKey(code, pressed) {
        if (!VALID_KEYS.has(code)) return
        if (pressed) { this.keys.add(code); if (code === 0) this.interruptPending |= 1 }
        else this.keys.delete(code)
    }
    releaseKeys() { this.keys.clear(); this.lastScan = 0 }
    readPort(port) {
        if (port === 1) {
            let result = 255
            for (const code of this.keys) if (code > 0) {
                const group = (code - 1) >> 3
                if (!(this.keyMask & (1 << group))) result &= ~(1 << ((code - 1) & 7))
            }
            return result
        }
        if (port === 3) return this.interruptMask
        if (port === 4) return this.interruptPending
        if (port === 6) return this.bank
        if (port === 0x20) return 0 // Standard 6 MHz CPU mode.
        if (port === 0x10) return this.lcd.status()
        if (port === 0x11) return this.lcd.read()
        throw new Error(`Unsupported TI port read ${hex(port, 2)}`)
    }
    writePort(port,value) {
        if (port === 1) this.keyMask = value
        else if (port === 3) { this.interruptMask = value; this.interruptPending &= value }
        else if (port === 6) this.bank = value & 0x7f
        else if (port === 0x10) this.lcd.command(value)
        else if (port === 0x11) this.lcd.write(value)
        else throw new Error(`Unsupported TI port write ${hex(port, 2)}`)
    }
    scanKey() {
        const held = [...this.keys].filter(code => code > 0)
        if (held.length !== 1) { this.lastScan = 0; return 0 }
        const code = held[0]
        const time = this.machine.tStateCount
        const repeat = [1,2,3,4,56].includes(code)
        if (code === this.lastScan && (!repeat || time < this.scanTime)) return 0
        this.scanTime = time + (code === this.lastScan ? 480_000 : 2_400_000)
        this.lastScan = code
        return code
    }
    character(code,advance = true) {
        const mem = this.machine.memory
        let row = mem[TI.curRow]
        let col = mem[TI.curCol]
        if (row >= 8 || col >= 16) throw new Error('Text cursor is outside the 16 × 8 character screen')
        if (code === 0xd || code === 0xa) { col = 0; row++ }
        else {
            const glyph = (FONT[String.fromCharCode(code).toUpperCase()] ?? FONT['?']).split('/')
            for (let y = 0; y < 8; y++) for (let x = 0; x < 6; x++) this.lcd.pixels[(row * 8 + y) * 96 + col * 6 + x] = Number(glyph[y]?.[x] ?? 0)
            if (advance) col++
            if (col >= 16) { col = 0; row++ }
        }
        if (row >= 8) { this.lcd.pixels.copyWithin(0, 96 * 8); this.lcd.pixels.fill(0, 96 * 56); row = 7 }
        if (advance) { mem[TI.curCol] = col; mem[TI.curRow] = row }
        this.lcd.dirty = true
    }
    serviceBcall() {
        const r = this.machine.z80.regs
        const mem = this.machine.memory
        const ret = mem[r.sp] | (mem[(r.sp + 1) & 65535] << 8)
        const call = mem[ret] | (mem[(ret + 1) & 65535] << 8)
        switch (call) {
            case 0x4018: r.a = this.scanKey(); break
            case 0x4501: this.character(r.a,false); break
            case 0x4504: this.character(r.a); break
            case 0x4507: for (const ch of String(r.hl).padStart(5,' ')) this.character(ch.charCodeAt(0)); break
            case 0x450a: {
                let count = 0
                while (mem[r.hl] !== 0 && count++ < 65536) { this.character(mem[r.hl]); r.hl = (r.hl + 1) & 65535 }
                if (count >= 65536) throw new Error('Unterminated string passed to _PutS')
                r.hl = (r.hl + 1) & 65535
                break
            }
            case 0x4540: this.lcd.clear(); break
            case 0x4546: this.lcd.clear(); mem.fill(0,0x8508,0x8508 + 128); break
            case 0x4558: mem[TI.curRow] = 0; mem[TI.curCol] = 0; break
            case 0x486a: this.lcd.copy(mem); break
            case 0x4bd0: mem.fill(0,TI.graph,TI.graph + 768); break
            case 0x4c33: for (let i = 0; i < r.bc; i++) mem[(r.hl + i) & 65535] = r.a; break
            default: throw new Error(`Unsupported TI OS call ${hex(call)} at ${hex((ret - 1) & 65535)}`)
        }
        r.sp = (r.sp + 2) & 65535
        r.pc = (ret + 2) & 65535
        this.machine.tStateCount += call === 0x486a ? 60000 : 800
    }
    runSlice(budget = 100000, single = false) {
        if (this.done) return
        const start = this.machine.tStateCount
        const deadline = performance.now() + 12
        let count = 0
        do {
            const r = this.machine.z80.regs
            if (r.pc === 0x28) this.serviceBcall()
            else if (r.pc < 0x8000 && r.pc !== 0x38 && r.pc !== 0x39) throw new Error(`Unsupported ROM execution at ${hex(r.pc)}`)
            else {
                const reason = this.machine.step()
                if (reason === StopReason.HALTED || reason === StopReason.RETURNED) { this.done = true; break }
                if (reason === StopReason.WAITING_FOR_INTERRUPT) {
                    if (!(this.interruptMask & 2)) break
                    this.machine.tStateCount = Math.max(this.machine.tStateCount,this.nextInterrupt)
                }
            }
            if (this.machine.tStateCount >= this.nextInterrupt) {
                this.interruptPending |= 2
                this.nextInterrupt = this.machine.tStateCount + 6_000_000 / 118
            }
            if (this.interruptPending & this.interruptMask & 3 && r.iff1) {
                this.machine.raiseInterrupt()
                this.interruptPending &= ~3
            }
            count++
        } while (!single && this.machine.tStateCount - start < budget && (count % 256 !== 0 || performance.now() < deadline))
        if (this.done && this.listErrorAddress !== undefined) {
            const code = this.machine.memory[this.listErrorAddress]
            if (code) throw new Error(({ 1: 'Array index out of range', 2: 'Cannot pop an empty array', 3: 'Heap is out of memory', 4: 'Value not found in array', 5: 'Remainder divisor cannot be zero', 6: 'min/max requires a non-empty sequence', 7: 'range step cannot be zero', 8: 'Division divisor cannot be zero', 9: 'randint lower bound exceeds upper bound', 10: 'sleep must be between 0 and 32.767 seconds', 11: 'Input position must leave room for text and a cursor', 12: 'Dictionary key not found', 13: 'Dictionary changed during iteration' })[code] ?? 'Runtime error')
        }
    }
}
export function hex(value,width = 4) { return '0x' + value.toString(16).toUpperCase().padStart(width,'0') }
