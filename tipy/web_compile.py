"""One JSON request on stdin -> one JSON response on stdout for the local server."""

import json
import re
import sys

from .errors import CompileError
from .project import compile_project


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("Send a JSON object with files and entry")
        files = request.get("files")
        entry = request.get("entry")
        if not isinstance(files, dict) or not 1 <= len(files) <= 64:
            raise ValueError("Provide between 1 and 64 source files")
        if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_]\w*\.(?:tip|asm|z80|obj)", name)
               or not isinstance(text, str) for name, text in files.items()):
            raise ValueError("Invalid source file name or contents")
        if not isinstance(entry, str) or not entry.endswith(".tip") or entry not in files:
            raise ValueError("Choose an existing .tip entry file")
        optimization = request.get("optimization", "aggressive")
        if not isinstance(optimization, str) or optimization not in {"reference", "balanced", "aggressive"}:
            raise ValueError("Choose reference, balanced, or aggressive optimization")
        result = compile_project(files, entry, optimization=optimization)
        print(json.dumps({"ok": True, "compiler": "python", **result}))
    except CompileError as error:
        print(json.dumps({"ok": False, "compiler": "python", "error": {
            "message": error.message, "filename": error.filename,
            "line": error.line, "column": error.column}}))
    except (ValueError, TypeError, AttributeError) as error:
        print(json.dumps({"ok": False, "error": {"message": str(error)}}))
    except RecursionError:
        print(json.dumps({"ok": False, "compiler": "python",
                          "error": {"message": "Program is too deeply nested"}}))


if __name__ == "__main__":
    main()
