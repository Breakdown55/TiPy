"""Editor bridge. User programs are compiled, never executed as Python."""
import json
import os
import re
import sys

sys.path.insert(0, os.environ.get("TIPY_COMPILER_ROOT") or os.path.dirname(__file__))
from tipy.errors import CompileError
from tipy.project import compile_project, parse_project
from tipy.checker import check


def main():
    try:
        request = json.load(sys.stdin)
        files, entry = request["files"], request["entry"]
        if not isinstance(files, dict) or not 1 <= len(files) <= 64:
            raise ValueError("Provide between 1 and 64 source files")
        if any(not re.fullmatch(r"[A-Za-z_]\w*\.(tip|obj)", name) or not isinstance(source, str)
               for name, source in files.items()):
            raise ValueError("Use simple .tip and .obj file names (letters, digits, underscores)")
        if sum(len(source.encode("utf-8")) for source in files.values()) > 1024 * 1024:
            raise ValueError("Project exceeds the 1 MB source limit")
        if entry not in files or not entry.endswith(".tip"):
            raise ValueError("Choose an existing .tip entry file")
        optimization = request.get("optimization", "aggressive")
        if optimization not in {"reference", "balanced", "aggressive"}:
            raise ValueError("Invalid optimization profile")
        if request.get("mode") == "check":
            tree, _ = parse_project(files, entry, optimization=optimization)
            check(tree)
            result = {}
        else:
            compiled = compile_project(files, entry, optimization=optimization)
            result = {"assembly": compiled["assembly"]}
        print(json.dumps({"ok": True, **result}))
    except CompileError as error:
        print(json.dumps({"ok": False, "error": {"message": error.message,
              "filename": error.filename, "line": error.line, "column": error.column}}))
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        print(json.dumps({"ok": False, "error": {"message": str(error)}}))


if __name__ == "__main__":
    main()
