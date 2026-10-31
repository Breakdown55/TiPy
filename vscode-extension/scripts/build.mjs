import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile, copyFile, cp, rm } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
process.chdir(resolve(here, '..'))
const require = createRequire(import.meta.url)
let esbuild
try { esbuild = require('esbuild') } catch { esbuild = require('../../asm-editor/ti84-runner/node_modules/esbuild') }
const names = ['machine', 'asm', 'base', 'inst', 'disasm', 'emulator', 'logger']
const alias = Object.fromEntries(names.map(name => [`z80-${name}`, resolve(`../asm-editor/packages/z80/packages/z80-${name}/src/index.ts`)]))
alias['@specy/z80'] = alias['z80-machine']
const rstFix = { name: 'z80-rst-vector-radix', setup(builder) {
    builder.onLoad({ filter: /z80-asm[\\/]src[\\/]Asm\.ts$/ }, async ({ path }) => {
        const source = await readFile(path, 'utf8')
        const original = 'const constValue = parseInt(token, 10);'
        if (!source.includes(original)) throw new Error('Review upstream assembler RST compatibility fix')
        return { contents: source.replace(original, 'const constValue = parseInt(token, tokenVariants[0].mnemonic === "rst" ? 16 : 10);'), loader: 'ts' }
    })
} }
await mkdir('dist', { recursive: true })
await esbuild.build({ entryPoints: ['src/extension.cjs'], outfile: 'dist/extension.cjs', bundle: true, platform: 'node', format: 'cjs', external: ['vscode'], sourcemap: true })
await esbuild.build({ entryPoints: ['src/emulator.js'], outfile: 'dist/emulator.js', bundle: true, format: 'iife', alias, plugins: [rstFix], minify: true })
await esbuild.build({ entryPoints: ['src/assembler.js'], outfile: 'dist/assembler.cjs', bundle: true, platform: 'node', format: 'cjs', alias, plugins: [rstFix] })
await rm('dist/compiler', { recursive: true, force: true })
await mkdir('dist/compiler', { recursive: true })
await cp('../tipy', 'dist/compiler/tipy', { recursive: true, filter: source => !/(?:^|[\\/])(?:tests|__pycache__)(?:[\\/]|$)/.test(source) })
await copyFile('python/bridge.py', 'dist/compiler/bridge.py')
await copyFile('../LICENSE', 'LICENSE')
await copyFile('../asm-editor/packages/z80/LICENSE', 'THIRD-PARTY-NOTICES.txt')
const python = process.env.TIPY_PYTHON || 'python'
await writeFile('dist/catalog.json', execFileSync(python, ['scripts/catalog.py'], { encoding: 'utf8', windowsHide: true }))
console.log('Built extension, Python compiler, language catalog, assembler, and ROM-free emulator.')
