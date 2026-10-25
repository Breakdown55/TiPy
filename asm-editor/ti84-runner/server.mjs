import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { resolve, extname, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const root = fileURLToPath(new URL('dist/', import.meta.url))
const workspace = resolve(root,'../../..')

function json(response,status,data) {
    response.writeHead(status,{ 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    response.end(JSON.stringify(data))
}

export function createApp({ python = process.env.TIPY_PYTHON || 'python' } = {}) {
return createServer(async (request, response) => {
    if (request.url === '/api/compile') {
        if (request.method !== 'POST') { json(response,405,{ error: { message: 'Use POST' } }); return }
        if (!request.headers['content-type']?.startsWith('application/json')) { json(response,415,{ error: { message: 'Send JSON' } }); return }
        if (request.headers.origin && request.headers.origin !== `http://${request.headers.host}`) { json(response,403,{ error: { message: 'Origin mismatch' } }); return }
        let body = ''
        request.setEncoding('utf8')
        try {
            for await (const chunk of request) {
                body += chunk
                if (Buffer.byteLength(body) > 1_000_000) { json(response,413,{ error: { message: 'Project must be smaller than 1 MB' } }); return }
            }
            JSON.parse(body)
        } catch { json(response,400,{ error: { message: 'Invalid JSON request' } }); return }
        // No shell and no execution of source code: only JSON passes to the compiler.
        const child = execFile(python,['-m','tipy.web_compile'],{
            cwd: workspace, timeout: 5000, maxBuffer: 4_000_000,
            windowsHide: true, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
        },(error,stdout) => {
            if (response.destroyed) return
            if (error) {
                const message = error.code === 'ENOENT' ? 'Python was not found. Set TIPY_PYTHON to your Python executable.'
                    : error.killed ? 'Python compilation timed out.' : 'Python compiler could not run. Check the server terminal.'
                json(response,503,{ error: { message } })
                console.error(error.message)
                return
            }
            try { const result = JSON.parse(stdout); json(response,result.ok ? 200 : 422,result) }
            catch { json(response,500,{ error: { message: 'Invalid response from Python compiler' } }) }
        })
        child.stdin.on('error',() => {})
        child.stdin.end(body)
        response.on('close',() => { if (!response.writableEnded) child.kill() })
        return
    }
    try {
        const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
        const path = resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`)
        if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) throw new Error('Invalid path')
        const data = await readFile(path)
        response.setHeader('Cache-Control','no-store')
        response.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.map': 'application/json' })[extname(path)] ?? 'application/octet-stream')
        response.end(data)
    } catch { response.writeHead(404).end('Not found') }
})
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    createApp().listen(8084,'127.0.0.1',() => console.log('TiPy · Python compiler · http://127.0.0.1:8084'))
}
