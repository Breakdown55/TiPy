import { Ti84Runner } from '../../asm-editor/ti84-runner/src/hardware.js'
export function measureAssembly(assembly) {
    const runner = new Ti84Runner(assembly)
    return { machineBytes: runner.machineBytes, codeDataBytes: runner.codeDataBytes, heapBytes: runner.heapBytes }
}
