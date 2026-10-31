const { test } = require('node:test')
const assert = require('node:assert/strict')
const { analyze, entries, splitParams, callAt, inCommentOrString } = require('../src/language.cjs')
const catalog = require('../dist/catalog.json')
const { importContext, inferType } = require('../src/language.cjs')
test('import contexts work before a prefix and between selected names', () => {
    assert.deepEqual(importContext('import '), { kind: 'module', prefix: '' })
    assert.deepEqual(importContext('from tim'), { kind: 'module', prefix: 'tim' })
    assert.deepEqual(importContext('from egx '), { kind: 'import-keyword' })
    assert.deepEqual(importContext('from egx import '), { kind: 'member', module: 'egx', prefix: '', selected: [] })
    assert.deepEqual(importContext('from egx import clear, ren'), { kind: 'member', module: 'egx', prefix: 'ren', selected: ['clear'] })
    assert.deepEqual(importContext('import egx as '), { kind: 'none' })
    assert.equal(importContext('pixel_on(1, '), null)
})
test('ordinary keywords and variable types appear in language suggestions', () => {
    const result = entries('score = 10\nint values = [1,2]\ndef double(int value) -> int:\n    return value*2\nanswer = double(score)\n', 5, null, catalog)
    assert.equal(result.find(item => item.name === 'while').kind, 'keyword')
    assert.equal(result.find(item => item.name === 'i16').kind, 'type')
    assert.equal(result.find(item => item.name === 'score').signature, 'score: int')
    assert.equal(result.find(item => item.name === 'values').signature, 'values: int[]')
    assert.equal(result.find(item => item.name === 'answer').signature, 'answer: int')
    assert.equal(inferType('unknown_function()'), undefined)
})
test('native API completion respects module aliases and actual exports', () => {
    const source = 'import egx as e\nimport random\ne.'
    const result = entries(source, 2, 'e', catalog)
    assert(result.some(item => item.name === 'draw_mesh' && item.params.includes('model')))
    assert(result.some(item => item.name === 'push_matrix' && item.params === ''))
    assert(!entries(source, 2, 'random', catalog).some(item => item.name === 'choice'))
    assert(entries(source, 2, 'random', catalog).some(item => item.name === 'randint'))
})
test('local functions, imports and bundled helper class methods', () => {
    const source = 'from timing import Clock\nclock = Clock()\ndef add(int x, int y=1) -> int:\n    return x+y\n'
    const sources = { timing: 'class Clock:\n    def __init__(self):\n        pass\n    def tick(self, int fps=0) -> int:\n        return 0\n' }
    assert.equal(entries(source, 4, 'clock', catalog, sources).find(item => item.name === 'tick').params, 'int fps=0')
    assert(entries(source, 4, null, catalog, sources).some(item => item.signature === 'add(int x, int y=1) -> int'))
    assert.equal(analyze(source).symbols.find(item => item.name === 'clock').instance, 'Clock')
})
test('container completion follows the declared/initial value type', () => {
    const source = 'int values = [1,2]\nstr name = "TiPy"\nscores = {"one":1}\n'
    assert(entries(source, 3, 'values', catalog).some(item => item.name === 'append'))
    assert(!entries(source, 3, 'name', catalog).some(item => item.name === 'append'))
    assert(entries(source, 3, 'name', catalog).some(item => item.name === 'upper'))
    assert(entries(source, 3, 'scores', catalog).some(item => item.name === 'items'))
})
test('parameter hints count nested calls and literals correctly', () => {
    assert.deepEqual(splitParams('tuple[int,int] p, str s="a,b", int x=2'), ['tuple[int,int] p', 'str s="a,b"', 'int x=2'])
    assert.equal(callAt('egx.draw_rect(min(1,2), 3, ').parameter, 2)
    assert.equal(callAt('egx.draw_rect(min(1,2), 3, ').name, 'egx.draw_rect')
    assert(inCommentOrString('print("x'))
    assert(inCommentOrString('# egx.'))
    assert(!inCommentOrString('print("x", '))
})
test('native defaults produce usable minimal completion snippets', () => {
    const values = entries('import egx', 0, 'egx', catalog)
    assert.equal(values.find(item => item.name === 'init_camera').params, 'i16 fov=75, i16 near=2, i16 far=120')
    assert.equal(values.find(item => item.name === 'scale').params, 'float factor')
    assert.equal(values.find(item => item.name === 'draw_shadow').params.split(',')[0], 'i16 model')
})
test('every shipped API and container method has useful documentation', () => {
    for (const item of [...catalog.builtins, ...Object.values(catalog.modules).flat()]) {
        assert(item.description?.length > 30, `Missing documentation for ${item.name}`)
        assert(!item.description.includes('Only reachable routines'), `Generic placeholder for ${item.name}`)
    }
    const source = 'int values = []\nstr text = ""\nscores = {}\n'
    for (const receiver of ['values', 'text', 'scores']) {
        for (const item of entries(source, 3, receiver, catalog)) assert(item.description?.length > 30, `${receiver}.${item.name}`)
    }
    assert.match(catalog.builtins.find(s => s.name === 'circle').description, /diameter/)
    assert.match(catalog.modules.egx.find(s => s.name === 'draw_circle').description, /radius/)
    assert.match(catalog.builtins.find(s => s.name === 'input').description, /blinking cursor/)
    assert.match(catalog.modules.egx.find(s => s.name === 'scale').description, /positive uniform/)
})
test('helper methods describe behavior and local functions retain their own comments', () => {
    const source = 'from timing import Clock\nclock = Clock()\n'
    const helpers = { ...catalog.helperSources }
    assert.match(entries(source, 2, 'clock', catalog, helpers).find(s => s.name === 'tick').description, /milliseconds/)
    assert.match(entries(source, 2, null, catalog, helpers).find(s => s.name === 'Clock').description, /elapsed time/)
    assert.match(entries(source, 2, 'clock', catalog, { timing: helpers.timing.replace(/\n/g, '\r\n') }).find(s => s.name === 'tick').description, /milliseconds/)
    const local = '# Doubles the score before applying the bonus.\ndef double(int value) -> int:\n    return value*2\n'
    assert.equal(analyze(local).symbols.find(s => s.name === 'double').description, 'Doubles the score before applying the bonus.')
    const overridden = { timing: '# Creates a game-specific countdown.\nclass Clock:\n    def __init__(self):\n        pass\n' }
    assert.match(entries(source, 2, null, catalog, overridden).find(s => s.name === 'Clock').description, /countdown/)
})
test('graphics namespace tooltips explain the public API choices', () => {
    const items = entries('import gx\nimport egx', 2, null, catalog)
    assert.match(items.find(s => s.name === 'gx').description, /Lower-level/)
    assert.match(items.find(s => s.name === 'egx').description, /Works on its own/)
    assert.deepEqual(Object.keys(catalog.modules).filter(name => name.endsWith('gx')).sort(), ['egx', 'gx'])
})
test('GX completion exposes primitives and keeps conveniences in EGX', () => {
    const source = 'import gx\nimport egx'
    const low = entries(source, 2, 'gx', catalog)
    const easy = entries(source, 2, 'egx', catalog)
    for (const name of ['begin', 'vertex', 'end', 'draw', 'shade', 'depth_test', 'cull', 'load_matrix', 'TRIANGLES', 'LINES', 'render']) assert(low.some(item => item.name === name), name)
    for (const name of ['draw_rect', 'print', 'input', 'load_obj', 'draw_mesh', 'translate', 'present']) assert(!low.some(item => item.name === name), name)
    assert(easy.some(item => item.name === 'draw_rect'))
    assert.equal(low.find(item => item.name === 'TRIANGLES').kind, 'constant')
    assert.match(low.find(item => item.name === 'vertex').description, /Submits an XY vertex/)
})
