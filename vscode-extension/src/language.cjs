function splitParams(text) {
    const parts = []; let depth = 0, start = 0, quote = ''
    for (let i = 0; i < text.length; i++) {
        const c = text[i]
        if (quote) { if (c === quote && text[i - 1] !== '\\') quote = ''; continue }
        if (c === '"' || c === "'") { quote = c; continue }
        if ('([{'.includes(c)) depth++
        if (')]}'.includes(c)) depth--
        if (c === ',' && depth === 0) { parts.push(text.slice(start, i).trim()); start = i + 1 }
    }
    if (text.slice(start).trim()) parts.push(text.slice(start).trim())
    return parts
}
function paramName(text) { return text.split('=')[0].trim().match(/\w+$/)?.[0] || 'value' }
const keywords = {
    if: 'Runs a block when its condition is true.', elif: 'Tests another condition after an if branch.', else: 'Runs the alternative block.',
    while: 'Repeats a block while its condition is true.', for: 'Iterates over a sequence or range.', in: 'Tests membership or selects the iterable in a for loop.',
    def: 'Declares a function. Parameters can have TiPy types and the return type follows ->.', return: 'Returns a value from a function.',
    class: 'Declares a class.', break: 'Exits the innermost loop.', continue: 'Starts the next iteration of the innermost loop.', pass: 'An empty statement.',
    import: 'Imports a supported native module or sibling .tip module.', from: 'Selects names to import from a module.', as: 'Assigns a local alias to an imported name.',
    and: 'True when both operands are truthy.', or: 'True when either operand is truthy.', not: 'Negates a truth value.', is: 'Tests identity, including None checks.',
    try: 'Starts an exception-handling block.', except: 'Handles an exception from a try block.', finally: 'Runs cleanup after a try block.',
    raise: 'Raises an exception.', assert: 'Raises an assertion error when its condition is false.', yield: 'Produces a value from a generator.', lambda: 'Creates an anonymous function.',
    True: 'Boolean true.', False: 'Boolean false.', None: 'The absence of a value.'
}
const declarationTypes = {
    int: 'Signed 16-bit integer; an alias for i16 (-32768..32767).', i16: 'Signed 16-bit integer (-32768..32767).',
    u8: 'Unsigned byte (0..255).', i32: 'Signed 32-bit integer.', bool: 'Boolean True or False.',
    str: 'Text string.', float: 'Floating-point number.', fixed: 'Fixed-point numeric value.', void: 'Function return type for a function with no result.'
}
function inferType(value, symbols = []) {
    value = value.trim().replace(/\s+#.*$/, '')
    if (/^(True|False)$/.test(value)) return 'bool'
    if (/^None$/.test(value)) return 'None'
    if (/^[+-]?(?:0[xX][\da-fA-F]+|\d+)$/.test(value)) return 'int'
    if (/^[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?$/.test(value)) return 'float'
    if (/^(?:f|r)?["']/.test(value)) return 'str'
    if (/^\[.*\]$/.test(value)) {
        const contents = value.slice(1, -1).split(/\s+for\s/)[0]
        const types = splitParams(contents).map(item => inferType(item, symbols))
        return types.length && types.every(type => type && type === types[0]) ? `${types[0]}[]` : undefined
    }
    if (/^\{.*\}$/.test(value)) {
        const first = splitParams(value.slice(1, -1))[0]
        const pair = first?.match(/^(.+?)\s*:\s*(.+)$/)
        if (pair) {
            const key = inferType(pair[1], symbols), element = inferType(pair[2], symbols)
            if (key && element) return `dict[${key}, ${element}]`
        }
    }
    const call = value.match(/^([A-Za-z_]\w*(?:\.\w+)?)\s*\(/)?.[1]
    const returns = { int: 'int', str: 'str', bool: 'bool', float: 'float', len: 'int', 'random.randint': 'int', key_down: 'bool', 'time.frame_ticks': 'int' }
    if (call && returns[call]) return returns[call]
    if (call === 'list' && /\brange\s*\(/.test(value)) return 'int[]'
    const known = symbols.filter(symbol => symbol.name === (call || value)).at(-1)
    if (known) return call ? known.returnType || (known.kind === 'class' ? known.name : undefined) : known.type
    // Keep inference conservative: do not label arbitrary mixed expressions.
    const arithmetic = value.match(/^([A-Za-z_]\w*|[+-]?\d+)\s*(?:\+|-|\*|\/\/|%)\s*([A-Za-z_]\w*|[+-]?\d+)$/)
    if (arithmetic) {
        const left = inferType(arithmetic[1], symbols), right = inferType(arithmetic[2], symbols)
        if (left && left === right) return left
    }
    return undefined
}
const methods = {
    list: { append: 'value', insert: 'int index, value', pop: 'int index=-1', remove: 'value', clear: '', extend: 'values', reverse: '', copy: '', count: 'value', index: 'value', sort: 'bool reverse=False' },
    str: { upper: '', lower: '', capitalize: '', title: '', swapcase: '', join: 'values', find: 'str sub, int start=0, int end=-1', rfind: 'str sub, int start=0, int end=-1', index: 'str sub, int start=0, int end=-1', rindex: 'str sub, int start=0, int end=-1', count: 'str sub, int start=0, int end=-1', startswith: 'str sub, int start=0, int end=-1', endswith: 'str sub, int start=0, int end=-1', replace: 'str old, str new, int count=-1', split: 'str sep=" ", int maxsplit=-1', strip: 'str chars=" "', lstrip: 'str chars=" "', rstrip: 'str chars=" "' },
    dict: { get: 'key, default', setdefault: 'key, default', pop: 'key', clear: '', copy: '', update: 'other', keys: '', values: '', items: '' }
}
function analyze(source) {
    const symbols = [], imports = {}, classes = {}; let owner = null
    const lines = source.split(/\r?\n/)
    function descriptionAt(row) {
        const comments = []
        for (let i = row - 1; i >= 0 && /^\s*#(?!define\b)/.test(lines[i]); i--) comments.unshift(lines[i].replace(/^\s*#\s?/, ''))
        if (!comments.length) {
            const indent = lines[row].match(/^\s*/)[0].length
            for (let i = row + 1; i < lines.length && /^\s*#/.test(lines[i]) && lines[i].match(/^\s*/)[0].length > indent; i++) comments.push(lines[i].replace(/^\s*#\s?/, ''))
        }
        return comments.join('\n') || undefined
    }
    lines.forEach((line, row) => {
        if (!line.trim() || line.trim().startsWith('#')) return
        const indent = line.match(/^\s*/)[0].length
        if (owner && indent <= owner.indent) owner = null
        let m
        if ((m = line.match(/^\s*import\s+(\w+)(?:\s+as\s+(\w+))?/))) imports[m[2] || m[1]] = { module: m[1] }
        if ((m = line.match(/^\s*from\s+(\w+)\s+import\s+(.+)/))) {
            for (const part of m[2].split('#')[0].split(',')) {
                const binding = part.trim().match(/^(\w+)(?:\s+as\s+(\w+))?$/)
                if (binding) imports[binding[2] || binding[1]] = { module: m[1], member: binding[1] }
            }
        }
        if ((m = line.match(/^\s*class\s+(\w+)/))) {
            owner = { name: m[1], indent }; classes[m[1]] = []
            symbols.push({ name: m[1], kind: 'class', params: '', signature: m[1] + '()', description: descriptionAt(row), row, column: line.indexOf(m[1]), indent })
        } else if ((m = line.match(/^\s*def\s+(\w+)\((.*)\)(?:\s*->\s*([^:]+))?\s*:/))) {
            const params = splitParams(m[2]).filter(p => p !== 'self').join(', ')
            const symbol = { name: m[1], params, returnType: m[3]?.trim(), signature: `${m[1]}(${params})${m[3] ? ' -> ' + m[3].trim() : ''}`, description: descriptionAt(row), kind: 'function', row, column: line.indexOf(m[1]), indent }
            if (owner) {
                classes[owner.name].push(symbol)
                if (m[1] === '__init__') Object.assign(symbols.find(s => s.name === owner.name && s.kind === 'class'), { params, signature: `${owner.name}(${params})` })
            } else symbols.push(symbol)
            for (const param of splitParams(m[2])) {
                const name = paramName(param)
                const type = param.split('=')[0].trim().match(/^(.+?)\s+[A-Za-z_]\w*$/)?.[1] || 'int'
                if (name !== 'self') symbols.push({ name, type, container: type.endsWith('[]') ? 'list' : type === 'str' ? 'str' : type.startsWith('dict[') ? 'dict' : null, kind: 'variable', signature: `${name}: ${type}`, row, column: line.indexOf(name), indent: indent + 4 })
            }
        } else if ((m = line.match(/^\s*(?:(?:int|i16|u8|bool|str|float|fixed|i32|\w+\[\])(?:\[\])*\s+)?([A-Za-z_]\w*)\s*=(?!=)\s*(.*)/))) {
            const value = m[2].trim(), declaration = line.trim().split('=')[0].trim()
            const container = /^(?:\[|list\()/.test(value) || /\[\]/.test(declaration) ? 'list' : /^(?:["']|str\()/.test(value) || /^str\s/.test(declaration) ? 'str' : /^\{|^dict\(/.test(value) ? 'dict' : null
            const explicitType = declaration === m[1] ? undefined : declaration.slice(0, declaration.lastIndexOf(m[1])).trim()
            const inferredType = inferType(value, symbols)
            const instance = m[2].match(/^([\w.]+)\(/)?.[1] || symbols.filter(s=>s.name===value).at(-1)?.instance
            const [moduleName, memberName] = (instance || '').split('.')
            const binding = imports[moduleName]
            const nativeShape = binding?.module === 'egx' && ['Cube', 'Plane'].includes(memberName || binding.member) ? `egx.${memberName || binding.member}` : undefined
            // TiPy permits `int values = [1, 2]`; [] establishes the array.
            const type = container === 'list' && explicitType && !explicitType.endsWith('[]') ? `${explicitType}[]` : explicitType || inferredType || nativeShape
            if (!['if', 'while', 'return'].includes(m[1])) symbols.push({ name: m[1], kind: 'variable', type, inferred: !explicitType && !!type, signature: type ? `${m[1]}: ${type}` : declaration, container, instance, row, column: line.indexOf(m[1]), indent })
        } else if ((m = line.match(/^\s*for\s+([\w, ]+)\s+in\s/))) {
            for (const name of m[1].split(',').map(s => s.trim())) symbols.push({ name, kind: 'variable', signature: name, row, column: line.indexOf(name), indent: indent + 4 })
        }
    })
    return { symbols, imports, classes }
}

function helperDescription(module, name, catalog, sources) {
    return catalog.helperSources?.[module] === sources[module]?.replace(/\r\n/g, '\n') ? catalog.helpers?.[`${module}.${name}`] : undefined
}
function entriesForModule(module, catalog, sources) {
    if (catalog.modules[module]) return catalog.modules[module]
    return sources[module] ? analyze(sources[module]).symbols.filter(s => s.indent === 0 && !s.name.startsWith('_')).map(s => ({ ...s, description: helperDescription(module, s.name, catalog, sources) || s.description })) : []
}
function importContext(before) {
    let match
    if ((match = before.match(/^\s*(?:import|from)\s+(\w*)$/))) return { kind: 'module', prefix: match[1] }
    if (/^\s*from\s+\w+\s+(?:i(?:m(?:p(?:o(?:r(?:t)?)?)?)?)?)?$/.test(before)) return { kind: 'import-keyword' }
    if ((match = before.match(/^\s*from\s+(\w+)\s+import\s+(.*)$/))) {
        const parts = match[2].split(',')
        const current = parts.pop().trim()
        if (!/^\w*$/.test(current)) return { kind: 'none' }
        return { kind: 'member', module: match[1], prefix: current, selected: parts.map(part => part.trim().split(/\s+as\s+/)[0]) }
    }
    if (/^\s*(?:import|from)\b/.test(before)) return { kind: 'none' }
    return null
}
function entries(source, row, receiver, catalog, sources = {}) {
    const parsed = analyze(source)
    if (receiver) {
        const binding = parsed.imports[receiver]
        if (binding && !binding.member) return entriesForModule(binding.module, catalog, sources)
        const instance = parsed.symbols.filter(s => s.name === receiver && s.row <= row).at(-1)
        if (instance?.container) return Object.entries(methods[instance.container]).map(([name, params]) => ({ name, params, signature: `${name}(${params})`, kind: 'function', description: catalog.containers?.[instance.container]?.[name] }))
        const type = instance?.instance || (receiver === 'self' ? Object.keys(parsed.classes).at(-1) : null)
        if (type) {
            const [first, second] = type.split('.')
            const imported = parsed.imports[first]
            if (imported) {
                const className = second || imported.member
                if (catalog.objects?.[`${imported.module}.${className}`]) return catalog.objects[`${imported.module}.${className}`]
                return (analyze(sources[imported.module] || '').classes[className] || []).map(s => ({ ...s, description: helperDescription(imported.module, `${className}.${s.name}`, catalog, sources) || s.description }))
            }
            if (parsed.classes[type]) return parsed.classes[type]
        }
        return []
    }
    const result = [...catalog.builtins,
        ...Object.entries(keywords).map(([name, description]) => ({ name, kind: 'keyword', signature: name, description })),
        ...Object.entries(declarationTypes).filter(([name]) => !catalog.builtins.some(item => item.name === name)).map(([name, description]) => ({ name, kind: 'type', signature: name, description }))]
    for (const [alias, binding] of Object.entries(parsed.imports)) {
        const item = binding.member ? entriesForModule(binding.module, catalog, sources).find(e => e.name === binding.member) : { name: alias, kind: 'module', signature: binding.module, description: catalog.moduleDescriptions?.[binding.module] }
        if (item) result.push({ ...item, name: alias, signature: item.signature?.replace(new RegExp('^' + binding.member + '\\b'), alias) })
    }
    result.push(...parsed.symbols.filter(s => s.kind === 'function' || s.kind === 'class' || s.row <= row))
    return [...new Map(result.map(item => [item.name, item])).values()]
}

function callAt(text) {
    const stack = []; let quote = '', comment = false
    for (let i = 0; i < text.length; i++) {
        const c = text[i]
        if (comment) { if (c === '\n') comment = false; continue }
        if (quote) { if (c === quote && text[i - 1] !== '\\') quote = ''; continue }
        if (c === '#') { comment = true; continue }
        if (c === '"' || c === "'") { quote = c; continue }
        if ('([{'.includes(c)) stack.push({ delimiter: c, name: c === '(' ? text.slice(0, i).match(/([A-Za-z_]\w*(?:\.\w+)?)\s*$/)?.[1] : null, parameter: 0 })
        else if (')]}'.includes(c)) stack.pop()
        else if (c === ',' && stack.length) stack.at(-1).parameter++
    }
    return [...stack].reverse().find(frame => frame.name)
}
function inCommentOrString(text) {
    let quote = ''
    for (let i = 0; i < text.length; i++) {
        const c = text[i]
        if (quote) { if (c === quote && text[i - 1] !== '\\') quote = '' }
        else if (c === '#') return true
        else if (c === '"' || c === "'") quote = c
    }
    return !!quote
}
module.exports = { analyze, entries, entriesForModule, splitParams, paramName, callAt, inCommentOrString, inferType, declarationTypes, importContext }
