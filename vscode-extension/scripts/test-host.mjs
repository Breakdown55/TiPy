import { mkdir, writeFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { createWriteStream } from 'node:fs'
process.chdir(resolve(dirname(fileURLToPath(import.meta.url)), '..'))
if (!process.env.VSCODE_EXECUTABLE) throw new Error('Set VSCODE_EXECUTABLE to an installed VS Code executable')
const folder = resolve('dist/host-workspace')
await mkdir(folder, { recursive: true })
await writeFile(resolve(folder, 'main.tip'), 'import utils\nimport egx\nclear()\npixel_on(utils.value(), 4)\nrender()\n')
await writeFile(resolve(folder, 'utils.tip'), 'def value() -> int:\n    return 1\n')
await mkdir('dist/test-extensions', { recursive: true })
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const child = spawn(process.env.VSCODE_EXECUTABLE, [
    '--extensionDevelopmentPath=' + resolve('dist/package'),
    '--extensionTestsPath=' + resolve('test/host.cjs'),
    '--user-data-dir=' + resolve('dist/host-profile'),
    '--extensions-dir=' + resolve('dist/test-extensions'),
    '--disable-extensions', '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes', '--disable-gpu', folder
], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env })
const log = createWriteStream(resolve('dist/host-test.log'))
child.stdout.pipe(log); child.stderr.pipe(log)
const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve) })
log.end()
console.log(`VS Code integration exit ${code}. Log: ${resolve('dist/host-test.log')}`)
if (code) process.exit(code)
