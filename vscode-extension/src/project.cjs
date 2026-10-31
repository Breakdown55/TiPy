const path = require('node:path')
const fs = require('node:fs/promises')
const { spawn } = require('node:child_process')

async function collectProject(entryPath, documents = []) {
    const folder = path.dirname(entryPath)
    const files = {}
    for (const item of await fs.readdir(folder, { withFileTypes: true })) {
        if (item.isFile() && /\.(tip|obj)$/.test(item.name)) {
            const filename = path.join(folder, item.name)
            const stat = await fs.stat(filename)
            if (stat.size > 1024 * 1024) throw new Error(`${item.name} exceeds the 1 MB source limit`)
            files[item.name] = await fs.readFile(filename, 'utf8')
        }
    }
    for (const document of documents) {
        if (document.uri.scheme === 'file' && path.dirname(document.uri.fsPath) === folder && /\.(tip|obj)$/.test(document.uri.fsPath)) {
            files[path.basename(document.uri.fsPath)] = document.getText()
        }
    }
    if (Object.keys(files).length > 64) throw new Error('A TiPy project supports at most 64 sibling source/OBJ files')
    if (Buffer.byteLength(JSON.stringify(files)) > 2 * 1024 * 1024 || Object.values(files).reduce((n, text) => n + Buffer.byteLength(text), 0) > 1024 * 1024) throw new Error('Project exceeds the 1 MB source limit')
    return { files, entry: path.basename(entryPath), folder }
}

function runPython(executable, args, request, options = {}) {
    return new Promise((resolve, reject) => {
        const child = spawn(executable, args, { cwd: options.cwd, windowsHide: true, shell: false,
            env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1', ...(options.env || {}) } })
        let stdout = '', stderr = '', settled = false
        const terminate = () => {
            if (process.platform === 'win32' && child.pid && child.exitCode === null) {
                // Some Windows Python launchers spawn the interpreter. Kill our
                // subprocess tree so its inherited pipes cannot keep VS Code busy.
                const killer = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'), ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, shell: false, stdio: 'ignore' })
                killer.on('error', () => child.kill())
                killer.on('close', code => { if (code) child.kill() })
            } else child.kill()
        }
        const finish = (error, value) => {
            if (settled) return
            settled = true
            clearTimeout(timer)
            options.signal?.removeEventListener('abort', abort)
            error ? reject(error) : resolve(value)
        }
        const abort = () => { terminate(); finish(new Error('Compilation cancelled')) }
        const timer = setTimeout(() => { terminate(); finish(new Error('Compiler timed out. Increase tipy.compileTimeout if needed.')) }, options.timeout || 30000)
        options.signal?.addEventListener('abort', abort, { once: true })
        if (options.signal?.aborted) abort()
        child.on('error', error => finish(error))
        child.stdout.on('data', data => {
            stdout += data.toString()
            if (stdout.length > 8 * 1024 * 1024) { terminate(); finish(new Error('Compiler output exceeds 8 MB')) }
        })
        child.stderr.on('data', data => { stderr = (stderr + data.toString()).slice(-16000) })
        child.on('close', code => {
            if (code !== 0) return finish(new Error(stderr.trim() || `Python exited with code ${code}`))
            try { finish(null, JSON.parse(stdout)) } catch { finish(new Error(`Invalid compiler response: ${stderr || stdout.slice(0, 400)}`)) }
        })
        child.stdin.on('error', () => {})
        child.stdin.end(JSON.stringify(request))
    })
}
module.exports = { collectProject, runPython }
