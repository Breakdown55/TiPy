import { DEFAULT_FILES } from './defaults.js'

const validName = name => typeof name === 'string' && /^[A-Za-z_]\w*\.(tip|asm|z80|obj)$/i.test(name) && name !== 'main.asm'
const runnable = name => typeof name === 'string' && /\.(tip|asm|z80)$/i.test(name)

export function loadProject(saved) {
    const candidate = saved?.files ?? saved
    const structured = saved?.files && typeof saved.files === 'object' && !Array.isArray(saved.files)
    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)
        || !structured && !Object.keys(candidate).length || !Object.values(candidate).every(value => typeof value === 'string')) {
        return { files: { ...DEFAULT_FILES }, entry: 'main.tip' }
    }
    const rename = name => name.replace(/\.(pti|tipy|pytie)$/i,'.tip')
    const files = {}
    // A saved .tip file wins over a legacy file of the same name.
    for (const [name,content] of Object.entries(candidate)) {
        const canonical = rename(name)
        if (!validName(canonical)) continue
        if (canonical === name || !Object.hasOwn(candidate,canonical)) files[canonical] = content
    }
    if (!Object.keys(files).length && Object.keys(candidate).length) return { files: { ...DEFAULT_FILES }, entry: 'main.tip' }
    const sources=Object.keys(files).filter(runnable)
    const requested = typeof saved?.entry === 'string' ? rename(saved.entry) : 'main.tip'
    const project = { files, entry: sources.includes(requested) ? requested : sources[0] ?? null }
    const deleted = saved?.deletedFile
    if (validName(deleted?.name) && typeof deleted.content === 'string' && !Object.hasOwn(files,deleted.name)) {
        project.deletedFile = { name: deleted.name, content: deleted.content,
            wasEntry: deleted.wasEntry === true,
            fallbackEntry: sources.includes(deleted.fallbackEntry) ? deleted.fallbackEntry : null }
    }
    return project
}

export function removeFile(project, name) {
    if (!Object.hasOwn(project.files,name)) return project
    const files = { ...project.files }
    const content = files[name]
    delete files[name]
    const entry = project.entry === name ? Object.keys(files).find(runnable) ?? null : project.entry
    return { files, entry, deletedFile: { name, content, wasEntry: project.entry === name, fallbackEntry: entry } }
}

export function restoreFile(project) {
    const deleted = project.deletedFile
    if (!deleted || Object.hasOwn(project.files,deleted.name)) return project
    const restoreEntry = runnable(deleted.name) && (!project.entry || deleted.wasEntry && project.entry === deleted.fallbackEntry)
    return { files: { ...project.files, [deleted.name]: deleted.content },
        entry: restoreEntry ? deleted.name : project.entry }
}
