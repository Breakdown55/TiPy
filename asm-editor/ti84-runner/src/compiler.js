// Small, explicit PyTie prototype. Unsupported syntax is a compile error.
export const DEFAULT_FILES = {
    'main.tip': `int x = 48
int y = 32

while True:
    clear()
    pixel(x, y)
    present()
    wait()

    if key("left"):
        x -= 1
    if key("right"):
        x += 1
    if key("up"):
        y -= 1
    if key("down"):
        y += 1
`,
    'utils.tip': '# Shared code goes here. Use import utils in main.tip.\n'
}
const KEY = { down: 1, left: 2, right: 3, up: 4, enter: 9, clear: 15, second: 54, alpha: 48 }
function uncomment(line) {
    let quote = null
    let escaped = false
    for (let i = 0; i < line.length; i++) {
        const ch = line[i]
        if (escaped) { escaped = false; continue }
        if (ch === '\\' && quote) { escaped = true; continue }
        if (quote) { if (ch === quote) quote = null }
        else if (ch === '"' || ch === "'") quote = ch
        else if (ch === '#') return line.slice(0,i)
    }
    return line
}
export function compilePyTie(files,entry = 'main.tip') {
    const lines = []
    const visited = new Set()
    const active = new Set()
    const fail = (line,message) => { throw new Error(`${line.file}:${line.line}: ${message}`) }
    function visit(name) {
        if (active.has(name)) throw new Error(`Circular import: ${name}`)
        if (visited.has(name)) return
        if (typeof files[name] !== 'string') throw new Error(`File not found: ${name}`)
        active.add(name)
        for (const [index,raw] of files[name].split(/\r?\n/).entries()) {
            const line = { file: name, line: index + 1, text: uncomment(raw).trim(), indent: raw.length - raw.trimStart().length }
            if (!line.text) continue
            if (/^\s*\t/.test(raw)) fail(line,'Use spaces for indentation')
            const imported = /^import ([A-Za-z_]\w*)$/.exec(line.text)
            if (imported) { if (line.indent) fail(line,'Imports must be at the top level'); visit(imported[1] + '.tip') }
            else lines.push(line)
        }
        active.delete(name)
        visited.add(name)
    }
    visit(entry)
    if (!lines.length) throw new Error('Nothing to compile')
    const variables = new Map()
    const code = []
    const data = []
    const used = new Set()
    let serial = 0
    let position = 0
    const label = () => `__L${serial++}`
    const emit = (...instructions) => code.push(...instructions.map(s => '        ' + s))
    const mark = name => code.push(name + ':')
    const bcall = name => emit('rst 0x28',`dw ${name}`)
    const variable = (name,line) => { const v = variables.get(name); if (!v) fail(line,`Unknown variable ${name}`); return v }
    function expression(text,line) {
        const tokens = text.match(/\s+|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|0x[\da-fA-F]+|\d+|[A-Za-z_]\w*|==|!=|<=|>=|[()+\-,<>]/g) ?? []
        if (tokens.join('') !== text) fail(line,'Unsupported expression')
        const ts = tokens.filter(t => !/^\s+$/.test(t))
        let index = 0
        const consume = expected => { if (ts[index++] !== expected) fail(line,`Expected ${expected}`) }
        const precedence = { '==': 1, '!=': 1, '<': 1, '>': 1, '<=': 1, '>=': 1, '+': 2, '-': 2 }
        function atom() {
            const token = ts[index++]
            if (token === undefined) fail(line,'Expected an expression')
            if (token === '(') { const value = binary(0); consume(')'); return value }
            if (token === '-' || token === '+') {
                const value = atom()
                if (value.type === 'bool') fail(line,'Expected an integer')
                if (token === '-') { emit('ex de,hl','ld hl,0','or a','sbc hl,de'); return { type: 'int', constant: value.constant === undefined ? undefined : -value.constant } }
                return value
            }
            if (token === 'not') { const value = atom(); if (value.type !== 'bool') fail(line,'not requires bool'); emit('ld a,l','xor 1','ld l,a'); return { type: 'bool' } }
            if (/^(\d+|0x[\da-fA-F]+)$/.test(token)) {
                const value = Number(token)
                if (value > 65535) fail(line,'Integer literal is too large')
                emit(`ld hl,${value}`)
                return { type: 'int', constant: value }
            }
            if (token === 'True' || token === 'False') { emit(`ld hl,${token === 'True' ? 1 : 0}`); return { type: 'bool' } }
            if (token === 'key' && ts[index] === '(') {
                consume('(')
                const keyName = ts[index++]
                if (!/^(["']).*\1$/.test(keyName ?? '')) fail(line,'key() expects a quoted key name')
                const key = KEY[keyName.slice(1,-1).toLowerCase()]
                if (!key) fail(line,'Unknown key name; use up, down, left, right, enter, clear, second or alpha')
                consume(')')
                const group = (key - 1) >> 3
                const bit = (key - 1) & 7
                const done = label()
                emit(`ld a,${255 ^ (1 << group)}`,'out (1),a','in a,(1)',`and ${1 << bit}`,'ld hl,0',`jr nz,${done}`,'inc l')
                mark(done)
                return { type: 'bool' }
            }
            const v = variable(token,line)
            if (v.type === 'u8' || v.type === 'bool') emit(`ld a,(${v.label})`,'ld l,a','ld h,0')
            else emit(`ld hl,(${v.label})`)
            return { type: v.type === 'bool' ? 'bool' : 'int' }
        }
        function binary(minimum) {
            let left = atom()
            while (precedence[ts[index]] >= minimum) {
                const op = ts[index++]
                emit('push hl')
                const right = binary(precedence[op] + 1)
                emit('ex de,hl','pop hl')
                if (op === '+' || op === '-') {
                    if (left.type === 'bool' || right.type === 'bool') fail(line,'Arithmetic requires integers')
                    emit(...(op === '+' ? ['add hl,de'] : ['or a','sbc hl,de']))
                    left = { type: 'int', constant: left.constant !== undefined && right.constant !== undefined ? (op === '+' ? left.constant + right.constant : left.constant - right.constant) : undefined }
                } else {
                    if (left.type !== right.type) fail(line,'Comparison types must match')
                    if (left.type === 'bool' && op !== '==' && op !== '!=') fail(line,'Use == or != to compare bool values')
                    if (op === '>' || op === '<=') emit('ex de,hl')
                    if (!['==','!='].includes(op)) emit('ld a,h','xor 128','ld h,a','ld a,d','xor 128','ld d,a')
                    emit('or a','sbc hl,de')
                    const done = label()
                    const branch = ({ '==': 'nz', '!=': 'z', '<': 'nc', '>': 'nc', '<=': 'c', '>=': 'c' })[op]
                    emit('ld hl,0',`jr ${branch},${done}`,'inc l')
                    mark(done)
                    left = { type: 'bool' }
                }
            }
            return left
        }
        const value = binary(0)
        if (index !== ts.length) fail(line,`Unexpected token ${ts[index]}`)
        return value
    }
    function store(v,value,line) {
        if ((v.type === 'bool') !== (value.type === 'bool')) fail(line,`Expected ${v.type === 'bool' ? 'bool' : 'an integer'}`)
        if (value.constant !== undefined) {
            const [min,max] = v.type === 'u8' ? [0,255] : [-32768,32767]
            if (value.constant < min || value.constant > max) fail(line,`Value is outside ${v.type === 'u8' ? 'u8 (0…255)' : 'i16 (−32768…32767)'}`)
        }
        if (v.type === 'u8' || v.type === 'bool') emit('ld a,l',`ld (${v.label}),a`)
        else emit(`ld (${v.label}),hl`)
    }
    function block(indent) {
        while (position < lines.length) {
            const line = lines[position]
            if (line.indent < indent) return
            if (line.indent > indent) fail(line,'Unexpected indentation')
            if (line.text === 'else:') return
            position++
            code.push(`; ${line.file}:${line.line}  ${line.text}`)
            const control = /^(while|if) (.+):$/.exec(line.text)
            if (control) {
                const start = label()
                const end = label()
                if (control[1] === 'while') mark(start)
                const condition = expression(control[2],line)
                if (condition.type !== 'bool') fail(line,'Conditions must be bool')
                emit('ld a,l','or a',`jp z,${end}`)
                const next = lines[position]
                if (!next || next.indent <= indent) fail(line,'Expected an indented block')
                block(next.indent)
                if (control[1] === 'while') emit(`jp ${start}`)
                if (control[1] === 'if' && lines[position]?.indent === indent && lines[position]?.text === 'else:') {
                    const after = label()
                    emit(`jp ${after}`)
                    mark(end)
                    position++
                    const alternative = lines[position]
                    if (!alternative || alternative.indent <= indent) fail(line,'Expected an indented else block')
                    block(alternative.indent)
                    mark(after)
                } else mark(end)
                continue
            }
            const declaration = /^(int|i16|u8|bool) ([A-Za-z_]\w*)\s*=\s*(.+)$/.exec(line.text)
            if (declaration) {
                const [,type,name,rhs] = declaration
                if (variables.has(name)) fail(line,`Variable ${name} is already declared`)
                if (['True','False','key','not'].includes(name)) fail(line,'Reserved variable name')
                // Evaluate before exposing the declaration, so x = x cannot read garbage.
                const value = expression(rhs,line)
                const v = { type: type === 'i16' ? 'int' : type, label: `__var_${variables.size}` }
                store(v,value,line)
                variables.set(name,v)
                continue
            }
            const assignment = /^([A-Za-z_]\w*)\s*(\+=|-=|=)\s*(.+)$/.exec(line.text)
            if (assignment) {
                const [,name,op,rhs] = assignment
                const v = variable(name,line)
                const value = expression(op === '=' ? rhs : `${name} ${op[0]} (${rhs})`,line)
                store(v,value,line)
                continue
            }
            if (line.text === 'pass') continue
            if (line.text === 'clear()') { bcall('_GrBufClr'); continue }
            if (line.text === 'present()') { bcall('_GrBufCpy'); continue }
            if (line.text === 'wait()') { used.add('wait'); emit('call __wait'); continue }
            const pixel = /^pixel\((.+),\s*(.+)\)$/.exec(line.text)
            if (pixel) {
                const x = expression(pixel[1],line)
                emit('push hl')
                const y = expression(pixel[2],line)
                if (x.type === 'bool' || y.type === 'bool') fail(line,'Pixel coordinates must be integers')
                const skip = label()
                emit('ld b,l','ld a,h','ld d,a','pop hl','ld a,h','or d',`jr nz,${skip}`)
                // Do not truncate large or negative coordinates into visible pixels.
                emit('ld c,l','call __pixel')
                mark(skip)
                used.add('pixel')
                continue
            }
            const print = /^print\((.*)\)$/.exec(line.text)
            if (print) {
                const arg = print[1].trim()
                if (/^"(?:\\.|[^"\\])*"$/.test(arg)) {
                    let text
                    try { text = JSON.parse(arg) } catch { fail(line,'Invalid string') }
                    if ([...text].some(ch => ch.charCodeAt(0) > 126 || ch.charCodeAt(0) === 0)) fail(line,'Text currently supports ASCII')
                    const name = label()
                    data.push(`${name}: db ${[...text].map(ch => ch === '\n' ? 13 : ch.charCodeAt(0)).concat([13,0]).join(',')}`)
                    emit(`ld hl,${name}`)
                    bcall('_PutS')
                } else {
                    if (expression(arg,line).type === 'bool') fail(line,'print() currently accepts text or integers')
                    used.add('number')
                    emit('call __number')
                }
                continue
            }
            fail(line,'Unsupported statement. Use typed variables, if/else, while, print(), pixel(), clear(), present(), wait() or import')
        }
    }
    block(0)
    if (position !== lines.length) fail(lines[position],'Unexpected else or indentation')
    emit('ret')
    if (used.has('wait')) code.push(`__wait:
        ld b,6
__wait_loop:
        halt
        djnz __wait_loop
        ret`)
    if (used.has('pixel')) code.push(`__pixel:
        ld a,b
        cp 64
        ret nc
        ld a,c
        cp 96
        ret nc
        ld l,b
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld de,plotSScreen
        add hl,de
        ld a,c
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
        ld a,c
        and 7
        ld b,a
        inc b
        ld a,1
__pixel_mask:
        rrca
        djnz __pixel_mask
        or (hl)
        ld (hl),a
        ret`)
    if (used.has('number')) code.push(`__number:
        bit 7,h
        jr z,__number_positive
        push hl
        ld a,45
        rst 0x28
        dw _PutC
        pop hl
        ex de,hl
        ld hl,0
        or a
        sbc hl,de
__number_positive:
        rst 0x28
        dw _DispHL
        ld a,13
        rst 0x28
        dw _PutC
        ret`)
    for (const v of variables.values()) data.push(`${v.label}: ${v.type === 'u8' || v.type === 'bool' ? 'db' : 'dw'} 0`)
    return '#include "ti84.inc"\n        org userMem\n' + code.join('\n') + '\n' + data.join('\n') + '\n'
}
