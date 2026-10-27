import { DEFAULT_FILES } from './defaults.js'

export function loadProject(saved) {
    const candidate = saved?.files ?? saved
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)
        || !Object.keys(candidate).length || !Object.values(candidate).every(value => typeof value === 'string')) {
        return { files: { ...DEFAULT_FILES }, entry: 'main.tip' }
    }
    const rename = name => name.replace(/\.(pti|tipy|pytie)$/i,'.tip')
    const files = {}
    // A saved .tip file wins over a legacy file of the same name.
    for (const [name,content] of Object.entries(candidate)) {
        const canonical = rename(name)
        if (!/^[A-Za-z_]\w*\.(tip|asm|z80|obj)$/i.test(canonical) || canonical === 'main.asm') continue
        if (canonical === name || !Object.hasOwn(candidate,canonical)) files[canonical] = content
    }
    if (!Object.keys(files).length) return { files: { ...DEFAULT_FILES }, entry: 'main.tip' }
    const sources=Object.keys(files).filter(name=>/\.(tip|asm|z80)$/i.test(name))
    if(!sources.length){files['main.tip']=DEFAULT_FILES['main.tip'];sources.push('main.tip')}
    const requested = typeof saved?.entry === 'string' ? rename(saved.entry) : 'main.tip'
    return { files, entry: sources.includes(requested) ? requested : sources[0] }
}
