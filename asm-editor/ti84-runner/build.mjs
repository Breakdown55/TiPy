import { build } from 'esbuild'
import { mkdir, copyFile, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

process.chdir(fileURLToPath(new URL('.', import.meta.url)))
const names = ['machine', 'asm', 'base', 'inst', 'disasm', 'emulator', 'logger']
const alias = Object.fromEntries(names.map(name => [`z80-${name}`, resolve(`../packages/z80/packages/z80-${name}/src/index.ts`)]))
alias['@specy/z80'] = alias['z80-machine']
// Upstream's instruction table spells RST vectors in hex, but its trie parses
// those literals as decimal. Apply this narrow build-time fix without leaving
// an untracked modification inside the Git submodule.
const rstFix = {
    name: 'z80-rst-vector-radix',
    setup(builder) {
        builder.onLoad({ filter: /z80-asm[\\/]src[\\/]Asm\.ts$/ },async ({ path }) => {
            const source = await readFile(path,'utf8')
            const original = 'const constValue = parseInt(token, 10);'
            if (!source.includes(original)) throw new Error('Upstream assembler changed: review the RST vector compatibility fix')
            return { contents: source.replace(original,'const constValue = parseInt(token, tokenVariants[0].mnemonic === "rst" ? 16 : 10);'), loader: 'ts' }
        })
    }
}
await mkdir('dist', { recursive: true })
await build({ entryPoints: ['src/app.js'], outfile: 'dist/app.js', bundle: true, format: 'esm', alias, plugins: [rstFix], sourcemap: true })
await build({ entryPoints: ['src/hardware.test.js'], outfile: 'dist/hardware.test.mjs', bundle: true, platform: 'node', format: 'esm', alias, plugins: [rstFix] })
await build({ entryPoints: ['src/python-compiler.test.js'], outfile: 'dist/python-compiler.test.mjs', bundle: true, platform: 'node', format: 'esm', alias, plugins: [rstFix] })
for (const file of ['index.html', 'style.css']) await copyFile(file, `dist/${file}`)
console.log('Built TI-84 runner using ASM Editor’s checked-out @specy/z80 sources')
