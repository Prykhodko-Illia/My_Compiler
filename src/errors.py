"""Compilation errors: every one carries the line:column it points at."""


class CompileError(Exception):
    """Carries a source position so we can print `line N:C: ...`."""
    def __init__(self, line, col, msg):
        super().__init__(msg)
        self.line = line
        self.col = col
        self.msg = msg


def error_at(token, msg):
    return CompileError(token.line, token.col, msg)
