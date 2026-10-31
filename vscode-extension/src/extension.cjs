const vscode = require('vscode')
const path = require('node:path')
const fs = require('node:fs/promises')
const crypto = require('node:crypto')
const { collectProject, runPython } = require('./project.cjs')
const language = require('./language.cjs')
const { emulatorHtml } = require('./webview.cjs')

function activate(context) {
    const output = vscode.window.createOutputChannel('TiPy')
    const diagnostics = vscode.languages.createDiagnosticCollection('tipy')
    const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 30)
    status.command = 'tipy.run'; status.tooltip = 'TiPy: Run in Emulator'
    const catalog = require(path.join(context.extensionPath, 'dist/catalog.json'))
    const { measureAssembly } = require(path.join(context.extensionPath, 'dist/assembler.cjs'))
    let panel, ready, resolveReady, lastCompilation, timer, checking, compiling, interpreter, revision = 0
    let lastSourceUri = vscode.window.activeTextEditor?.document.languageId === 'tipy' ? vscode.window.activeTextEditor.document.uri : undefined
    const sources = {}
    const builtinSources = {}
    const config = uri => vscode.workspace.getConfiguration('tipy', uri)
    const emitter = new vscode.EventEmitter()
    const assemblyUri = vscode.Uri.parse('tipy-assembly:/output.z80')
    const assemblyChanged = new vscode.EventEmitter()
    const register = (name, callback) => vscode.commands.registerCommand(name, async (...args) => {
        try { return await callback(...args) }
        catch (error) { output.appendLine(error.stack || error.message); vscode.window.showErrorMessage(`TiPy: ${error.message}`); return undefined }
    })
    function trust() {
        if (!vscode.workspace.isTrusted) throw new Error('Trust this workspace to compile and run TiPy programs.')
    }
    function entryUri(uri) {
        if (!uri || uri.scheme !== 'file' || !uri.fsPath.endsWith('.tip')) {
            const active = vscode.window.activeTextEditor?.document.uri
            uri = active?.scheme === 'file' && active.fsPath.endsWith('.tip') ? active : lastSourceUri
        }
        if (!uri || uri.scheme !== 'file' || !uri.fsPath.endsWith('.tip')) throw new Error('Open a saved .tip file first.')
        const setting = config(uri).get('entryFile', '')
        if (setting) {
            const workspace = vscode.workspace.getWorkspaceFolder(uri)
            if (!workspace) throw new Error('Open a folder to use tipy.entryFile.')
            const target = path.resolve(workspace.uri.fsPath, setting)
            const relative = path.relative(workspace.uri.fsPath, target)
            if (relative.startsWith('..') || path.isAbsolute(relative) || !target.endsWith('.tip')) throw new Error('tipy.entryFile must name a .tip file inside the workspace folder.')
            return vscode.Uri.file(target)
        }
        return uri
    }
    async function python(uri) {
        const explicit = config(uri).get('pythonPath', '')
        if (explicit) return { executable: explicit, prefix: [] }
        if (interpreter) return interpreter
        const candidates = process.platform === 'win32' ? [['python', []], ['py', ['-3']], ['python3', []]] : [['python3', []], ['python', []]]
        for (const [executable, prefix] of candidates) {
            try {
                await runPython(executable, [...prefix, '-I', '-c', 'import sys,json; assert sys.version_info >= (3,10); print(json.dumps({"ok":True}))'], {}, { timeout: 5000 })
                return interpreter = { executable, prefix }
            } catch { /* Try the next installed interpreter. */ }
        }
        throw new Error('Python 3.10+ was not found. Install Python or set tipy.pythonPath to its executable.')
    }
    async function invoke(project, uri, mode, signal) {
        trust()
        const selected = await python(uri)
        if (signal.aborted) throw new Error('Compilation cancelled')
        const settings = config(uri)
        const compiler = path.join(context.extensionPath, 'dist/compiler')
        const override = settings.get('compilerPath', '')
        return runPython(selected.executable, [...selected.prefix, '-I', path.join(compiler, 'bridge.py')], {
            files: project.files, entry: project.entry, mode, optimization: settings.get('optimization', 'aggressive')
        }, { cwd: compiler, signal, timeout: settings.get('compileTimeout', 30000), env: override ? { TIPY_COMPILER_ROOT: override } : {} })
    }
    function reportError(error, project, uri) {
        diagnostics.clear()
        const target = error.filename && project.files[error.filename] !== undefined ? vscode.Uri.file(path.join(project.folder, error.filename)) : uri
        const row = Math.max(0, (error.line || 1) - 1), column = Math.max(0, (error.column || 1) - 1)
        const diagnostic = new vscode.Diagnostic(new vscode.Range(row, column, row, column + 1), error.message, vscode.DiagnosticSeverity.Error)
        diagnostic.source = 'TiPy'; diagnostics.set(target, [diagnostic])
    }
    async function compile(uri, run = false) {
        trust()
        const entry = entryUri(uri)
        clearTimeout(timer); checking?.abort(); compiling?.abort()
        const current = new AbortController(); compiling = current
        const version = ++revision
        status.text = '$(sync~spin) TiPy: Compiling'; status.show()
        try {
            const project = await collectProject(entry.fsPath, vscode.workspace.textDocuments)
            const result = await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: 'TiPy: Compiling', cancellable: true }, async (_, token) => {
                const listener = token.onCancellationRequested(() => current.abort())
                try { return await invoke(project, entry, 'compile', current.signal) } finally { listener.dispose() }
            })
            if (version !== revision || current.signal.aborted) return
            if (!result.ok) { reportError(result.error, project, entry); throw new Error(`${result.error.filename || project.entry}:${result.error.line || 1}: ${result.error.message}`) }
            const sizes = measureAssembly(result.assembly)
            const latest = await collectProject(entry.fsPath, vscode.workspace.textDocuments)
            if (JSON.stringify(latest.files) !== JSON.stringify(project.files)) throw new Error('Source changed during compilation. Compile again to use the latest edits.')
            diagnostics.clear()
            lastCompilation = { ...result, ...sizes, entry: project.entry, uri: entry.toString() }
            assemblyChanged.fire(assemblyUri)
            const label = `Compiled · ${sizes.machineBytes.toLocaleString()} bytes (${sizes.codeDataBytes.toLocaleString()} code/data + ${sizes.heapBytes.toLocaleString()} heap)`
            status.text = '$(check) TiPy: ' + sizes.machineBytes.toLocaleString() + ' bytes'
            output.appendLine(`${project.entry}: ${label}`)
            vscode.window.setStatusBarMessage(label, 8000)
            if (run) {
                await openEmulator()
                if (version !== revision || current.signal.aborted) return
                await panel.webview.postMessage({ type: 'run', assembly: result.assembly, filename: project.entry })
            }
            return lastCompilation
        } catch (error) {
            if (current.signal.aborted || version !== revision) return
            status.text = '$(error) TiPy: Compile failed'
            throw error
        } finally { if (compiling === current) compiling = null }
    }
    async function openEmulator() {
        if (!panel) {
            ready = new Promise(resolve => { resolveReady = resolve })
            panel = vscode.window.createWebviewPanel('tipy.emulator', 'TiPy Emulator', vscode.ViewColumn.Beside, {
                enableScripts: true, retainContextWhenHidden: true,
                localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'dist'), vscode.Uri.joinPath(context.extensionUri, 'media')]
            })
            const nonce = crypto.randomBytes(24).toString('hex')
            const script = panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'dist/emulator.js'))
            const style = panel.webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'media/emulator.css'))
            panel.webview.html = emulatorHtml({ cspSource: panel.webview.cspSource, script, style, nonce })
            panel.webview.onDidReceiveMessage(message => {
                if (message?.type === 'ready') resolveReady?.()
                if (['started', 'finished', 'error'].includes(message?.type)) {
                    emitter.fire(message)
                    if (message.type === 'error') { output.appendLine('Emulator: ' + String(message.message).slice(0, 2000)); output.show(true) }
                }
            }, null, context.subscriptions)
            panel.onDidChangeViewState(event => { if (!event.webviewPanel.visible) event.webviewPanel.webview.postMessage({ type: 'stop' }) }, null, context.subscriptions)
            panel.onDidDispose(() => { panel = null; resolveReady?.(); resolveReady = null }, null, context.subscriptions)
        } else panel.reveal(vscode.ViewColumn.Beside)
        await Promise.race([ready, new Promise((_, reject) => { const timeout = setTimeout(() => reject(new Error('Emulator did not start. Close its tab and try Run again.')), 15000); timeout.unref?.() })])
        if (!panel) throw new Error('Emulator was closed')
    }
    async function refreshSources(document) {
        if (!document || document.uri.scheme !== 'file') return
        try {
            await libraryReady
            const project = await collectProject(document.uri.fsPath, vscode.workspace.textDocuments)
            for (const name of Object.keys(sources)) delete sources[name]
            Object.assign(sources, builtinSources)
            for (const [name, source] of Object.entries(project.files)) if (name.endsWith('.tip')) sources[name.slice(0, -4)] = source
        } catch { /* Language tools remain available even for an oversized project. */ }
    }
    async function checkDocument(document) {
        if (!vscode.workspace.isTrusted || !config(document.uri).get('liveDiagnostics', true) || compiling) return
        checking?.abort(); const current = new AbortController(); checking = current
        const version = document.version
        try {
            const entry = entryUri(document.uri)
            const project = await collectProject(entry.fsPath, vscode.workspace.textDocuments)
            const result = await invoke(project, entry, 'check', current.signal)
            if (current.signal.aborted || version !== document.version || compiling) return
            if (result.ok) diagnostics.clear()
            else reportError(result.error, project, entry)
        } catch (error) { if (!current.signal.aborted) output.appendLine('Check: ' + error.message) }
    }
    function schedule(document) {
        if (document.languageId !== 'tipy') return
        clearTimeout(timer); checking?.abort()
        refreshSources(document)
        timer = setTimeout(() => checkDocument(document), config(document.uri).get('diagnosticDelay', 600))
    }
    const selector = [{ language: 'tipy', scheme: 'file' }, { language: 'tipy', scheme: 'untitled' }]
    function resolveEntry(document, position, fullName) {
        const parts = fullName.split('.')
        return language.entries(document.getText(), position.line, parts.length > 1 ? parts[0] : null, catalog, sources).find(item => item.name === parts.at(-1))
    }
    context.subscriptions.push(output, diagnostics, status, emitter, assemblyChanged,
        register('tipy.compile', uri => compile(uri)),
        register('tipy.run', uri => compile(uri, true)),
        register('tipy.stop', () => panel?.webview.postMessage({ type: 'stop' })),
        register('tipy.setEntry', async uri => {
            uri ||= vscode.window.activeTextEditor?.document.uri
            if (!uri || uri.scheme !== 'file' || !uri.fsPath.endsWith('.tip')) throw new Error('Select a saved .tip file.')
            const workspace = vscode.workspace.getWorkspaceFolder(uri)
            if (!workspace) throw new Error('Open a workspace folder before setting an entry file.')
            await config(uri).update('entryFile', path.relative(workspace.uri.fsPath, uri.fsPath).split(path.sep).join('/'), vscode.ConfigurationTarget.WorkspaceFolder)
            vscode.window.setStatusBarMessage(`TiPy entry: ${path.basename(uri.fsPath)}`, 5000)
            schedule(await vscode.workspace.openTextDocument(uri))
        }),
        register('tipy.showAssembly', async () => {
            if (!lastCompilation) throw new Error('Compile a .tip file first.')
            await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(assemblyUri), { viewColumn: vscode.ViewColumn.Beside, preview: true })
        }),
        vscode.workspace.registerTextDocumentContentProvider('tipy-assembly', { onDidChange: assemblyChanged.event, provideTextDocumentContent: () => lastCompilation?.assembly || '' }),
        vscode.languages.registerCompletionItemProvider(selector, { async provideCompletionItems(document, position, token, completionContext) {
            const before = document.lineAt(position.line).text.slice(0, position.character)
            if (language.inCommentOrString(before)) return []
            const importing = language.importContext(before)
            if (importing?.kind === 'none') return []
            if (completionContext?.triggerKind === vscode.CompletionTriggerKind.TriggerCharacter && [' ', ','].includes(completionContext.triggerCharacter) && !importing) return []
            await refreshSources(document)
            if (importing?.kind === 'module') return [...new Set([...Object.keys(catalog.modules), ...Object.keys(sources)])].filter(name => !name.startsWith('_') && name !== path.basename(document.uri.path, '.tip')).map(name => {
                const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Module)
                item.documentation = catalog.moduleDescriptions?.[name]
                item.sortText = name
                return item
            })
            if (importing?.kind === 'import-keyword') {
                const item = new vscode.CompletionItem('import', vscode.CompletionItemKind.Keyword)
                item.insertText = 'import '
                item.command = { title: 'Suggest exported names', command: 'editor.action.triggerSuggest' }
                return [item]
            }
            const from = importing?.kind === 'member'
            const receiver = before.match(/([A-Za-z_]\w*)\.\w*$/)?.[1]
            const declarationPosition = /^\s*[A-Za-z_]*$/.test(before)
            const options = from ? language.entriesForModule(importing.module, catalog, sources).filter(item => !importing.selected.includes(item.name)) : language.entries(document.getText(), position.line, receiver, catalog, sources)
            return options.map(entry => {
                const typeDeclaration = declarationPosition && Object.hasOwn(language.declarationTypes, entry.name)
                const kind = typeDeclaration || entry.kind === 'type' ? vscode.CompletionItemKind.TypeParameter : entry.kind === 'constant' ? vscode.CompletionItemKind.Constant : entry.kind === 'keyword' ? vscode.CompletionItemKind.Keyword : entry.kind === 'module' ? vscode.CompletionItemKind.Module : entry.kind === 'variable' ? vscode.CompletionItemKind.Variable : entry.kind === 'class' ? vscode.CompletionItemKind.Class : vscode.CompletionItemKind.Function
                const item = new vscode.CompletionItem(entry.name, kind)
                item.detail = typeDeclaration ? `${entry.name} (type)` : entry.signature
                item.documentation = typeDeclaration ? language.declarationTypes[entry.name] : entry.description
                if (!from && !typeDeclaration && entry.params != null && document.lineAt(position.line).text[position.character] !== '(') {
                    const required = language.splitParams(entry.params).filter(p => !p.includes('='))
                    item.insertText = new vscode.SnippetString(entry.name + '(' + required.map((param, i) => '${' + (i + 1) + ':' + language.paramName(param) + '}').join(', ') + ')$0')
                    item.command = { title: 'Show parameter hints', command: 'editor.action.triggerParameterHints' }
                }
                return item
            })
        } }, '.', ' ', ','),
        vscode.languages.registerHoverProvider(selector, { async provideHover(document, position) {
            const range = document.getWordRangeAtPosition(position, /[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?/)
            if (!range) return
            await refreshSources(document)
            const entry = resolveEntry(document, position, document.getText(range))
            if (!entry) return
            const markdown = new vscode.MarkdownString().appendCodeblock(entry.signature || entry.name, 'tipy')
            // Keep ordinary spaces so long descriptions wrap in narrow hovers.
            // Escape markup from source comments; command links stay untrusted.
            if (entry.description) markdown.appendMarkdown('\n\n' + entry.description.replace(/[\\`*_[\]<>]/g, '\\$&'))
            return new vscode.Hover(markdown, range)
        } }),
        vscode.languages.registerSignatureHelpProvider(selector, { async provideSignatureHelp(document, position) {
            const call = language.callAt(document.getText(new vscode.Range(new vscode.Position(0, 0), position)))
            if (!call) return
            await refreshSources(document)
            const entry = resolveEntry(document, position, call.name)
            if (!entry || entry.params == null) return
            const signature = new vscode.SignatureInformation(entry.signature, entry.description)
            signature.parameters = language.splitParams(entry.params).map(param => new vscode.ParameterInformation(param))
            const help = new vscode.SignatureHelp(); help.signatures = [signature]; help.activeSignature = 0
            help.activeParameter = Math.min(call.parameter, Math.max(0, signature.parameters.length - 1))
            return help
        } }, '(', ','),
        vscode.languages.registerInlayHintsProvider(selector, { provideInlayHints(document, range) {
            if (!config(document.uri).get('inlayTypeHints', true)) return []
            return language.analyze(document.getText()).symbols.filter(item => item.inferred && item.type && item.row >= range.start.line && item.row <= range.end.line).map(item => {
                const hint = new vscode.InlayHint(new vscode.Position(item.row, item.column + item.name.length), `: ${item.type}`, vscode.InlayHintKind.Type)
                hint.paddingLeft = false; hint.paddingRight = false
                hint.tooltip = 'Inferred by TiPy editor language tools. Explicit declarations do not receive a duplicate type label.'
                return hint
            })
        } }),
        vscode.languages.registerDocumentSymbolProvider(selector, { provideDocumentSymbols(document) {
            return language.analyze(document.getText()).symbols.filter(item => item.kind !== 'variable' || item.indent === 0).map(item => {
                const range = document.lineAt(item.row).range
                return new vscode.DocumentSymbol(item.name, item.signature, item.kind === 'function' ? vscode.SymbolKind.Function : item.kind === 'class' ? vscode.SymbolKind.Class : vscode.SymbolKind.Variable, range, range)
            })
        } }),
        vscode.workspace.onDidChangeTextDocument(event => schedule(event.document)),
        vscode.workspace.onDidOpenTextDocument(schedule),
        vscode.window.onDidChangeActiveTextEditor(editor => { if (editor?.document.languageId === 'tipy') { lastSourceUri = editor.document.uri; status.show(); schedule(editor.document) } else if (!compiling) status.hide() }),
        vscode.workspace.onDidChangeConfiguration(event => { if (event.affectsConfiguration('tipy')) { interpreter = null; checking?.abort(); if (!config().get('liveDiagnostics', true)) diagnostics.clear(); const doc = vscode.window.activeTextEditor?.document; if (doc) schedule(doc) } }),
        { dispose() { clearTimeout(timer); checking?.abort(); compiling?.abort(); panel?.dispose() } }
    )
    status.text = '$(play) TiPy'
    const libraryReady = fs.readdir(path.join(context.extensionPath, 'dist/compiler/tipy/lib')).then(async names => {
        for (const name of names) if (name.endsWith('.tip')) builtinSources[name.slice(0, -4)] = await fs.readFile(path.join(context.extensionPath, 'dist/compiler/tipy/lib', name), 'utf8')
        Object.assign(sources, builtinSources)
    }).catch(error => output.appendLine(error.message))
    if (vscode.window.activeTextEditor?.document.languageId === 'tipy') { status.show(); schedule(vscode.window.activeTextEditor.document) }
    return { onDidChangeEmulatorState: emitter.event }
}
module.exports = { activate }
