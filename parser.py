"""Phase 1: tokens -> AST  (syntax errors only)

One statement per line, so each line of tokens is parsed on its own:
  declaration:  i32 [mut] name { expr }
  assignment:   name := expr
  exit:         exit operand
  expr:         operand [ (+ | - | *) operand ]
  operand:      number | name
"""

from ast_nodes import Assign, BinOp, Const, Declare, Exit, Var
from errors import error_at
from lexer import Token

I32_MAX = 2**31 - 1
OPERATORS = {"plus", "minus", "times"}


def describe(token):
    if token.kind == "endline":
        return "the end of the line"
    if token.kind == "keyword":
        return f"keyword '{token.text}'"
    return f"'{token.text}'"


class LineParser:
    """A cursor over the tokens of one line. The line always ends with an
    endline token, so peek() never runs past the end."""

    def __init__(self, tokens):
        last = tokens[-1]
        if last.kind != "endline":           # last line of a file without a trailing newline
            tokens = tokens + [Token("endline", "", last.line, last.col + len(last.text))]
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        token = self.tokens[self.pos]
        if token.kind != "endline":
            self.pos += 1
        return token

    def at(self, kind, sub=None):
        token = self.peek()
        return token.kind == kind and (sub is None or token.sub == sub)

    def expect(self, kind, sub, what):
        if not self.at(kind, sub):
            raise error_at(self.peek(), f"expected {what}, found {describe(self.peek())}")
        return self.advance()

    def statement(self):
        first = self.peek()
        if self.at("keyword", "typename"):
            stmt = self.declaration()
        elif self.at("keyword", "statement"):
            self.advance()
            stmt = Exit(self.operand(), first)
        elif self.at("identifier"):
            target = self.advance()
            self.expect("operator", "assign", f"':=' after '{target.text}'")
            stmt = Assign(target, self.expr(), first)
        else:
            raise error_at(first, f"a statement must start with 'i32', 'exit' or a variable, found {describe(first)}")
        self.end()
        return stmt

    def declaration(self):
        first = self.advance()               # i32
        mutable = self.at("keyword", "specifier")
        if mutable:
            self.advance()
        name = self.expect("identifier", None, "a variable name")
        if not self.at("block", "start"):
            raise error_at(name, f"variable '{name.text}' needs an initialiser in {{}}")
        self.advance()
        init = self.expr()
        self.expect("block", "end", "'}'")
        return Declare(name, mutable, init, first)

    def expr(self):
        left = self.operand()
        if self.peek().sub not in OPERATORS:
            return left
        op = self.advance()
        right = self.operand()
        if self.peek().sub in OPERATORS:
            raise error_at(self.peek(), "only one operation (+, -, *) is allowed in an expression")
        return BinOp(op.text, left, right)

    def operand(self):
        token = self.peek()
        if token.kind == "constant":
            if int(token.text) > I32_MAX:
                raise error_at(token, f"constant {token.text} does not fit in i32 (max {I32_MAX})")
            self.advance()
            return Const(int(token.text), token)
        if token.kind == "identifier":
            self.advance()
            return Var(token.text, token)
        raise error_at(token, f"expected a number or a variable, found {describe(token)}")

    def end(self):
        token = self.peek()
        if token.kind != "endline":
            raise error_at(token, f"unexpected {describe(token)} after the end of the statement")


def parse(token_lines):
    """Return a list of statement nodes, skipping blank lines."""
    stmts = []
    for tokens in token_lines:
        if all(t.kind == "endline" for t in tokens):
            continue
        stmts.append(LineParser(tokens).statement())
    return stmts


def end_of_input(token_lines):
    """Position just past the last token, for errors about the program as a whole."""
    if not token_lines:
        return 1, 1
    last = token_lines[-1][-1]
    return last.line, last.col + (0 if last.kind == "endline" else len(last.text))
