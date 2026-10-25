class CompileError(Exception):
    """A source error, rather than an internal compiler exception."""

    def __init__(self, message, node):
        self.message = message
        self.line = node["line"]
        self.column = node["column"]
        self.filename = node.get("filename")
        super().__init__(f"{self.line}:{self.column}: {message}")
