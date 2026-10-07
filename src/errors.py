"""Compilation errors: every one carries the line:column it points at."""


class CompileError(Exception):
    """Carries a source position so we can print `line N:C: ...`."""
    def __init__(self, line, col, msg):
        super().__init__(msg)
        self.line = line
        self.col = col
        self.msg = msg


def error_at(where, msg):
    """An error at a token (in the lexer) or at an AST node (in the walk over the tree):
    both keep a line and a column."""
    return CompileError(where.line, where.col, msg)
