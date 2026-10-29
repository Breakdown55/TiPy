"""Connect the stages; retain intermediate outputs so they can be inspected."""

from .lexer import tokenize
from .parser import parse
from .checker import check
from .codegen import generate
from .optimization_pipeline import optimize_program


def compile_source(
    source, *, optimize_graphics=True, optimization="aggressive", assets=None
):
    from .stdlib import imports, resolve

    source, bindings = imports(source)
    import re

    if re.search(r"^\s*(?:import \w+|from \w+ import .+)", source, re.M):
        from .project import parse_project

        # Source imports use the same resolver as the browser, including
        # project overrides and ordinary editable library modules.
        tree, tokens = parse_project(
            {"main.tip": source, **(assets or {})},
            "main.tip",
            optimization=optimization,
        )
        resolve(tree, {"main.tip": bindings})
    else:
        tokens = tokenize(source)
        tree = parse(tokens)
        tree["assets"] = assets or {}
        tree["fast_wireframes"] = optimization == "aggressive"
        resolve(tree, {None: bindings})
    checked, symbols = check(tree)
    optimized, emitted_symbols, report = optimize_program(
        checked, symbols, optimization
    )
    assembly = generate(optimized, emitted_symbols, optimize_graphics=optimize_graphics)
    from .assembly_size import estimate

    return {
        "tokens": tokens,
        "ast": tree,
        "checked": checked,
        "optimized": optimized,
        "optimization_report": report,
        "source_symbols": symbols,
        "symbols": emitted_symbols,
        "record_symbols": checked.get("record_symbols", {}),
        "assembly": assembly,
        "estimated_bytes": estimate(assembly),
    }
