import { execFileSync } from 'node:child_process'
import { cp, mkdir, readFile, writeFile, copyFile, rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
process.chdir(root)
execFileSync(process.execPath, ['scripts/build.mjs'], { stdio: 'inherit' })
const staging = resolve('dist/package')
await rm(staging, { recursive: true, force: true })
await mkdir(staging, { recursive: true })
for (const folder of ['src', 'python', 'syntaxes', 'snippets', 'media']) await cp(folder, resolve(staging, folder), { recursive: true })
await mkdir(resolve(staging, 'dist'), { recursive: true })
for (const file of ['extension.cjs', 'extension.cjs.map', 'emulator.js', 'assembler.cjs', 'catalog.json']) await copyFile(resolve('dist', file), resolve(staging, 'dist', file))
await cp('dist/compiler', resolve(staging, 'dist/compiler'), { recursive: true })
for (const file of ['package.json', 'language-configuration.json', 'LICENSE', 'THIRD-PARTY-NOTICES.txt']) await copyFile(file, resolve(staging, file))
await copyFile('USAGE.md', resolve(staging, 'README.md'))
await writeFile(resolve(staging, '.vscodeignore'), '**/__pycache__/**\n**/*.pyc\n')
const { version } = JSON.parse(await readFile('package.json', 'utf8'))
const target = resolve(`dist/tipy-${version}.vsix`)
execFileSync(process.execPath, [resolve('node_modules/@vscode/vsce/vsce'), 'package', '--no-dependencies', '--allow-missing-repository', '--out', target], { cwd: staging, stdio: 'inherit' })
console.log(target)
