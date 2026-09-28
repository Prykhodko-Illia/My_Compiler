"""Phase 1: tokens -> AST  (syntax errors only)

Recursive descent, written by hand: one method per rule of grammar.ebnf.

  program   ::= { statement } exit
  statement ::= decl | assign
  decl      ::= type [ "mut" ] ident "{" expr "}"
  type      ::= "i32" | "i64" | "bool"
  assign    ::= ident ":=" expr
  exit      ::= "exit" factor
  expr      ::= arith [ ( "==" | "!=" ) arith ]
  arith     ::= term { ( "+" | "-" ) term }
  term      ::= factor { "*" factor }
  factor    ::= number | "true" | "false" | ident

A comparison binds weaker than arithmetic, which is why expr sits above arith: in
x * 2 != y + 5 each side is a whole arithmetic chain. There is no loop in expr, so at
most one comparison per expression.

peek() returns the current token of the current line, or None at the end of the line;
eat() returns it and moves on. One token of look-ahead decides every choice: the parser
never scans ahead through the line. It reads tokens only, never the source text, and
puts no token into the tree.
"""

from .ast_nodes import (AssignNode, BinOpNode, BoolNode, ConstNode, DeclNode,
                        ExitNode, ProgramNode, VarNode)
from .errors import CompileError

I32_MAX = 2**31 - 1


def describe(token):
    if token is None:
        return "end of line"
    if token.kind == "keyword":
        return f"keyword '{token.text}'"
    return f"'{token.text}'"


class Parser:
    def __init__(self, token_lines):
        self.lines = token_lines          # [[Token]] from the lexer, one list per line
        self.toks, self.pos = [], 0       # the tokens of the line being parsed
        self.end = (1, 1)                 # just past the last token of that line

    # -- reading the current line ------------------------------------------

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        token = self.toks[self.pos]
        self.pos += 1
        return token

    def at(self, kind, sub=None):
        token = self.peek()
        return token is not None and token.kind == kind and (sub is None or token.sub == sub)

    def error(self, msg, token=None):
        """An error at `token`, or -- when the line ended too early -- at the column
        right after its last token, where something should have been typed."""
        token = token or self.peek()
        if token is None:
            return CompileError(self.end[0], self.end[1], msg)
        return CompileError(token.line, token.col, msg)

    def expect(self, kind, sub, what):
        if not self.at(kind, sub):
            raise self.error(f"expected {what}, found {describe(self.peek())}")
        return self.eat()

    def start_line(self, tokens):
        self.toks = [t for t in tokens if t.kind != "endline"]
        self.pos = 0
        last = self.toks[-1]
        self.end = (last.line, last.col + len(last.text))

    # -- one method per rule ------------------------------------------------

    def parse_program(self):
        """program ::= { statement } exit"""
        statements, exit_node = [], None
        for tokens in self.lines:
            if all(t.kind == "endline" for t in tokens):       # blank line
                continue
            self.start_line(tokens)
            first = self.peek()

            if exit_node is not None:
                raise self.error("'exit' must be the last statement", first)
            if self.at("keyword", "statement"):
                exit_node = self.parse_exit()
            else:
                statements.append(self.parse_statement())

            if self.peek() is not None:                        # the line must be empty now
                raise self.error(f"unexpected {describe(self.peek())} after the statement")

        if exit_node is None:
            raise CompileError(self.end[0], self.end[1], "program has no exit statement")
        head = statements[0] if statements else exit_node
        return ProgramNode(head.line, head.col, statements, exit_node)

    def parse_statement(self):
        """statement ::= decl | assign"""
        if self.at("keyword", "typename"):
            return self.parse_decl()
        if self.at("identifier"):
            return self.parse_assign()
        raise self.error(f"a statement must start with a type name, 'exit' or a variable, found {describe(self.peek())}")

    def parse_decl(self):
        """decl ::= type [ "mut" ] ident "{" expr "}"     type ::= "i32" | "i64" | "bool" """
        type_token = self.eat()                                # i32 | i64 | bool
        mutable = self.at("keyword", "specifier")
        if mutable:
            self.eat()
        name = self.expect("identifier", None, "a variable name")
        if not self.at("block", "start"):
            raise self.error(f"variable '{name.text}' needs an initialiser in {{}}", name)
        self.eat()                                             # {
        init = self.parse_expr()
        self.expect("block", "end", "'}'")
        return DeclNode(name.line, name.col, name.text, type_token.text, mutable, init)

    def parse_assign(self):
        """assign ::= ident ":=" expr"""
        name = self.eat()
        self.expect("operator", "assign", f"':=' after '{name.text}'")
        return AssignNode(name.line, name.col, name.text, self.parse_expr())

    def parse_exit(self):
        """exit ::= "exit" factor -- a constant or a variable, never an operation"""
        keyword = self.eat()
        return ExitNode(keyword.line, keyword.col, self.parse_factor())

    def parse_expr(self):
        """expr ::= arith [ ( "==" | "!=" ) arith ]"""
        node = self.parse_arith()
        if self.at("operator", "eq") or self.at("operator", "ne"):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_arith())
            if self.at("operator", "eq") or self.at("operator", "ne"):
                raise self.error("only one comparison is allowed in an expression")
        return node

    def parse_arith(self):
        """arith ::= term { ( "+" | "-" ) term }"""
        node = self.parse_term()
        while self.at("operator", "plus") or self.at("operator", "minus"):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_term())
        return node

    def parse_term(self):
        """term ::= factor { "*" factor }"""
        node = self.parse_factor()
        while self.at("operator", "times"):
            op = self.eat()
            node = BinOpNode(op.line, op.col, op.text, node, self.parse_factor())
        return node

    def parse_factor(self):
        """factor ::= number | "true" | "false" | ident"""
        token = self.peek()
        if token is not None and token.kind == "constant":
            # TODO (Task 2): the semantic pass decides a constant's type (i32, else i64).
            if int(token.text) > I32_MAX:
                raise self.error(f"constant {token.text} does not fit in i32 (max {I32_MAX})", token)
            self.eat()
            return ConstNode(token.line, token.col, int(token.text))
        if self.at("keyword", "boolean"):
            self.eat()
            return BoolNode(token.line, token.col, token.text == "true")
        if token is not None and token.kind == "identifier":
            self.eat()
            return VarNode(token.line, token.col, token.text)
        raise self.error(f"expected a constant or a variable, found {describe(token)}")


def parse(token_lines):
    """Return the ProgramNode for a lexed source."""
    return Parser(token_lines).parse_program()
