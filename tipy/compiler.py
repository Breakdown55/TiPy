"""Connect the stages; retain intermediate outputs so they can be inspected."""

from .lexer import tokenize
from .parser import parse
from .checker import check
from .codegen import generate
from .optimization_pipeline import optimize_program


def compile_source(source, *, optimize_graphics=True, optimization="aggressive"):
    from .stdlib import imports, resolve
    source, bindings = imports(source)
    tokens = tokenize(source)
    tree = parse(tokens)
    resolve(tree, {None: bindings})
    checked, symbols = check(tree)
    optimized, emitted_symbols, report = optimize_program(checked, symbols, optimization)
    assembly = generate(optimized, emitted_symbols, optimize_graphics=optimize_graphics)
    from .assembly_size import estimate
    return {"tokens": tokens, "ast": tree, "checked": checked,
            "optimized": optimized, "optimization_report": report, "source_symbols": symbols,
            "symbols": emitted_symbols, "assembly": assembly,"estimated_bytes":estimate(assembly)}
