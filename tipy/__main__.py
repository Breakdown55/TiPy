import argparse
import json
from pathlib import Path
import sys

from .compiler import compile_source
from .errors import CompileError
from .lexer import tokenize
from .parser import parse


def main():
    parser = argparse.ArgumentParser(description="Compile TiPy declarations and expressions to Z80")
    parser.add_argument("source", help="Source file, or - to read stdin")
    parser.add_argument("-o", "--output", type=Path, help="Write assembly to this file")
    parser.add_argument("--optimization", choices=["reference", "balanced", "aggressive"], default="aggressive")
    parser.add_argument("--show", choices=["tokens", "ast", "checked", "optimized", "optimization_report", "source_symbols", "symbols", "assembly", "all"],
                        default="assembly", help="Inspect a compiler stage (JSON except assembly)")
    args = parser.parse_args()
    try:
        source = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8-sig")
        # Inspect an early stage even when a later stage would reject the source.
        if not args.output and args.show in {"tokens", "ast"}:
            tokens = tokenize(source)
            print(json.dumps(tokens if args.show == "tokens" else parse(tokens), indent=2))
            return 0
        result = compile_source(source, optimization=args.optimization)
        if args.output:
            args.output.write_text(result["assembly"], encoding="utf-8")
        if args.show == "assembly":
            if not args.output:
                print(result["assembly"], end="")
        else:
            print(json.dumps(result if args.show == "all" else result[args.show], indent=2))
    except CompileError as error:
        print(f"{args.source}:{error}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as error:
        print(f"TiPy: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
