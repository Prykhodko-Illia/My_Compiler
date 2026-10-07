"""Phase 1: tokens -> AST  (syntax errors only)

Recursive descent, written by hand: one method per rule of grammar.ebnf.

  program   ::= { statement } exit
  statement ::= decl | assign | if
  decl      ::= type [ "mut" ] ident "{" expr "}"
  type      ::= "i32" | "i64" | "bool"
  assign    ::= ident ":=" expr
  if        ::= "if" expr NL block [ "else" NL block ]
  block     ::= "{" NL { statement } [ exit NL ] "}" NL       (not empty)
  exit      ::= "exit" factor
  expr      ::= arith [ ( "==" | "!=" ) arith ]
  arith     ::= term { ( "+" | "-" ) term }
  term      ::= factor { "*" factor }
  factor    ::= number | "true" | "false" | ident | "!" factor

A comparison binds weaker than arithmetic, which is why expr sits above arith: in
x * 2 != y + 5 each side is a whole arithmetic chain. There is no loop in expr, so at
most one comparison per expression. '!' sits inside factor, so it binds tighter than
everything: !a == b compares !a with b.

Two cursors. The line cursor walks the source line by line: peek_line() returns the
first token of the next non-blank line, next_line() makes that line the current one.
The token cursor walks the current line: peek() returns its current token, or None at
the end of the line; eat() returns it and moves on. A declaration, an assignment and
an exit are one line each; an if is several -- its condition line, the '{' line, every
line of the block, the '}' line, and the same again for an else.

One token of look-ahead decides every choice: the parser never scans ahead through a
line. It reads tokens only, never the source text, and puts no token into the tree.
"""

from .ast_nodes import (AssignNode, BinOpNode, BlockNode, BoolNode, ConstNode,
                        DeclNode, ExitNode, IfNode, NotNode, ProgramNode, VarNode)
from .errors import CompileError


def describe(token):
    if token is None:
        return "end of line"
    if token.kind == "keyword":
        return f"keyword '{token.text}'"
    return f"'{token.text}'"


class Parser:
    def __init__(self, token_lines):
        self.lines = token_lines          # [[Token]] from the lexer, one list per line
        self.next = 0                     # index of the next line the line cursor gives out
        self.toks, self.pos = [], 0       # the tokens of the line being parsed
        self.end = (1, 1)                 # just past the last token of that line

    # -- the line cursor ----------------------------------------------------

    def peek_line(self):
        """The first token of the next non-blank line, or None at the end of the file."""
        while self.next < len(self.lines) and all(t.kind == "endline" for t in self.lines[self.next]):
            self.next += 1                                     # a blank line carries nothing
        return self.lines[self.next][0] if self.next < len(self.lines) else None

    def next_line(self):
        """Make the line peek_line() looked at the current one."""
        self.peek_line()
        self.toks = [t for t in self.lines[self.next] if t.kind != "endline"]
        self.pos = 0
        self.next += 1
        last = self.toks[-1]
        self.end = (last.line, last.col + len(last.text))

    # -- the token cursor: reading the current line --------------------------

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        token = self.toks[self.pos]
        self.pos += 1
        return token

    def at(self, kind, sub=None, text=None):
        return is_token(self.peek(), kind, sub, text)

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

    def end_line(self, after):
        """The current line must have nothing left on it."""
        if self.peek() is not None:
            raise self.error(f"unexpected {describe(self.peek())} after {after}")

    # -- one method per rule ------------------------------------------------

    def parse_program(self):
        """program ::= { statement } exit"""
        statements, exit_node = self.parse_lines(None)
        if exit_node is None:
            raise CompileError(self.end[0], self.end[1], "program has no exit statement")
        head = statements[0] if statements else exit_node
        return ProgramNode(head.line, head.col, statements, exit_node)

    def parse_lines(self, brace):
        """{ statement } [ exit ] -- the body of the program (brace is None) or of the block
        opened by `brace`, which stops at the line that starts with '}'. An exit is the last
        line of either: only the '}' may follow it."""
        statements, exit_node = [], None
        while True:
            first = self.peek_line()
            if first is None or (brace is not None and is_token(first, "block", "end")):
                return statements, exit_node
            if exit_node is not None:
                if brace is None:
                    raise self.error("'exit' must be the last statement", first)
                raise self.error("statement after 'exit' in the same block", first)
            self.next_line()
            if self.at("keyword", "statement"):
                exit_node = self.parse_exit()
                self.end_line("the statement")
            else:
                statements.append(self.parse_statement())

    def parse_statement(self):
        """statement ::= decl | assign | if"""
        if self.at("keyword", "branch", "if"):
            return self.parse_if()                             # it reads its own lines
        if self.at("keyword", "typename"):
            node = self.parse_decl()
        elif self.at("identifier"):
            node = self.parse_assign()
        elif self.at("keyword", "branch", "else"):
            raise self.error("'else' without an 'if'")
        elif self.at("block", "start"):
            raise self.error("a block must follow an 'if' or an 'else'")
        elif self.at("block", "end"):
            raise self.error("'}' without a '{'")
        else:
            raise self.error(f"a statement must start with a type name, 'if', 'exit' or a variable, found {describe(self.peek())}")
        self.end_line("the statement")
        return node

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

    def parse_if(self):
        """if ::= "if" expr NL block [ "else" NL block ]"""
        keyword = self.eat()
        condition = self.parse_expr()
        self.end_line("the statement")                         # if b {  -- the '{' has a line of its own
        then_block = self.parse_block("'if'")
        else_block = None
        if is_token(self.peek_line(), "keyword", "branch", "else"):
            self.next_line()
            self.eat()                                         # else
            self.end_line("'else'")
            else_block = self.parse_block("'else'")
        return IfNode(keyword.line, keyword.col, condition, then_block, else_block)

    def parse_block(self, owner):
        """block ::= "{" NL { statement } [ exit NL ] "}" NL   -- at least one line inside"""
        first = self.peek_line()
        if not is_token(first, "block", "start"):
            got = describe(first) if first is not None else "end of file"
            raise self.error(f"expected '{{' on its own line after {owner}, got {got}", first)
        self.next_line()
        brace = self.eat()
        self.end_line("'{'")
        statements, exit_node = self.parse_lines(brace)
        if self.peek_line() is None:
            raise self.error("'{' is never closed", brace)
        if not statements and exit_node is None:
            raise self.error("empty block", brace)
        self.next_line()
        self.eat()                                             # }
        self.end_line("'}'")
        return BlockNode(brace.line, brace.col, statements, exit_node)

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
        """factor ::= number | "true" | "false" | ident | "!" factor"""
        token = self.peek()
        if token is not None and token.kind == "constant":
            # How large a constant may be is a type question: the semantic pass decides,
            # because the answer depends on the type it lands in.
            self.eat()
            return ConstNode(token.line, token.col, int(token.text))
        if self.at("keyword", "boolean"):
            self.eat()
            return BoolNode(token.line, token.col, token.text == "true")
        if token is not None and token.kind == "identifier":
            self.eat()
            return VarNode(token.line, token.col, token.text)
        if self.at("operator", "not"):
            self.eat()
            return NotNode(token.line, token.col, self.parse_factor())
        raise self.error(f"expected a constant or a variable, found {describe(token)}")


def is_token(token, kind, sub=None, text=None):
    return (token is not None and token.kind == kind
            and (sub is None or token.sub == sub) and (text is None or token.text == text))


def parse(token_lines):
    """Return the ProgramNode for a lexed source."""
    return Parser(token_lines).parse_program()
