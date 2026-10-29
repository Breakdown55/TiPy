import chair from '../../../examples/chair.obj'
import gallery from '../../../examples/lumen_gallery.obj'

export const BUNDLED_ASSETS = { 'chair.obj': chair, 'lumen_gallery.obj': gallery }

// Only the exact, unedited sample from before world-coordinate scenes needs
// migration. A user-authored OBJ with the same name must stay untouched.
const PREVIOUS_GALLERY = '81d1c887c65e1c4fcc527a5abffb901315110b2c5d993a23fe22545859fa159d'

async function isPreviousGallery(text) {
    if (!text.includes('# Named render objects use exact world/local units.') || !globalThis.crypto?.subtle) return false
    const bytes = new TextEncoder().encode(text.replace(/\r\n/g,'\n').trim())
    const digest = await crypto.subtle.digest('SHA-256',bytes)
    return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('') === PREVIOUS_GALLERY
}

export async function addBundledAssets(project) {
    const files = { ...project.files }
    const seen = new Set(project.bundledAssets ?? [])
    const usesWorldScene = Object.entries(files).some(([name,text]) => /\.tip$/i.test(name) && /\bload_scene\s*\(\s*(?:file\s*=\s*)?["']lumen_gallery\.obj["']/.test(text))
    for (const [name,content] of Object.entries(BUNDLED_ASSETS)) {
        if (Object.hasOwn(files,name)) {
            if (name === 'lumen_gallery.obj' && usesWorldScene && await isPreviousGallery(files[name])) files[name] = content
        } else if (!seen.has(name) && project.deletedFile?.name !== name) {
            files[name] = content
        }
        seen.add(name)
    }
    return { ...project, files, bundledAssets: [...seen] }
}
