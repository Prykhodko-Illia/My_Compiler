import sys
from dataclasses import dataclass
from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)
I32_MAX = 2**31 - 1


class CompileError(Exception):
    """Carries a source position so we can print `line N:C: ...`."""
    def __init__(self, line, col, msg):
        super().__init__(msg)
        self.line = line
        self.col = col
        self.msg = msg


def error_at(token, msg):
    return CompileError(token.line, token.col, msg)


# ---------------------------------------------------------------------------
# Phase 0: bytes -> tokens  (lexical errors only)
#
# A hand-written state machine: one loop, one byte per step. The state says
# what we are in the middle of (nothing, a word, a number, a ':'), the byte's
# class says what happens next. A byte that ends a token is read again in
# START (`continue` without advancing) -- the only byte ever re-examined.
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str        # keyword | identifier | constant | block | operator | endline
    text: str
    line: int
    col: int         # 1-based position of the token's first byte
    sub: str = ""    # typename | specifier | statement | numeric | start | end | assign | plus | minus | times

    def __str__(self):
        text = "\\n" if self.kind == "endline" else self.text
        fields = ", ".join(f for f in (text, self.kind, self.sub) if f)
        return f"({fields}) {self.line}:{self.col}"


KEYWORDS = {b"i32": "typename", b"mut": "specifier", b"exit": "statement"}

SINGLE_BYTE = {                  # tokens that are complete after one byte
    ord("{"): ("block", "start"),
    ord("}"): ("block", "end"),
    ord("+"): ("operator", "plus"),
    ord("-"): ("operator", "minus"),
    ord("*"): ("operator", "times"),
}

SPACE, TAB, NEWLINE = 32, 9, 10
COLON, EQUALS, LBRACE, RBRACE = ord(":"), ord("="), ord("{"), ord("}")


def is_alpha(b):
    return 65 <= b <= 90 or 97 <= b <= 122 or b == 95      # A-Z a-z _


def is_digit(b):
    return 48 <= b <= 57                                   # 0-9


def show_byte(b):
    return repr(chr(b)) if 33 <= b <= 126 else f"0x{b:02x}"


def lex(data: bytes):
    """Return a list of lines, each a list of tokens ending with an endline token
    (the last line may lack it if the file has no trailing newline)."""
    lines, tokens = [], []
    open_braces = []                         # '{' tokens not yet closed on this line
    state, start, start_col = "START", 0, 1
    line, col = 1, 1
    i = 0
    while i <= len(data):                    # one extra step: the end of input
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None or b == NEWLINE:
                if open_braces:
                    raise error_at(open_braces[-1], "'{' is not closed before the end of the line")
                if b is None:
                    break
                tokens.append(Token("endline", "\n", line, col))
                lines.append(tokens)
                tokens = []
                line, col = line + 1, 0      # col becomes 1 at the bottom of the loop
            elif b in (SPACE, TAB):
                pass
            elif is_alpha(b):
                state, start, start_col = "IDENT", i, col
            elif is_digit(b):
                state, start, start_col = "NUMBER", i, col
            elif b == COLON:
                state, start_col = "COLON", col
            elif b in SINGLE_BYTE:
                kind, sub = SINGLE_BYTE[b]
                token = Token(kind, chr(b), line, col, sub)
                tokens.append(token)
                if b == LBRACE:
                    open_braces.append(token)
                elif b == RBRACE and open_braces:
                    open_braces.pop()        # a '}' with no '{' is the parser's problem
            elif b == EQUALS:
                raise CompileError(line, col, "unexpected byte '=': assignment is ':='")
            else:
                raise CompileError(line, col, f"unexpected byte {show_byte(b)}")

        elif state == "IDENT":
            if b is not None and (is_alpha(b) or is_digit(b)):
                pass
            else:                            # the word is complete: keyword or identifier?
                word = data[start:i]
                if word in KEYWORDS:
                    tokens.append(Token("keyword", word.decode(), line, start_col, KEYWORDS[word]))
                else:
                    tokens.append(Token("identifier", word.decode(), line, start_col))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                pass
            elif b is not None and is_alpha(b):
                raise CompileError(line, start_col, f"invalid number {data[start:i + 1].decode()!r}: a letter inside a number")
            else:
                tokens.append(Token("constant", data[start:i].decode(), line, start_col, "numeric"))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "COLON":
            if b == EQUALS:
                tokens.append(Token("operator", ":=", line, start_col, "assign"))
                state = "START"
            else:
                raise CompileError(line, start_col, "':' must be followed by '='")

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines


# ---------------------------------------------------------------------------
# AST nodes
#
# Expressions produce a value:  Const | Var | BinOp
# Statements are one per line:  Declare | Assign | Exit
# Nodes keep the tokens they came from, so every error can point at line:column.
# ---------------------------------------------------------------------------

@dataclass
class Const:
    value: int
    token: Token

@dataclass
class Var:
    name: str
    token: Token

@dataclass
class BinOp:
    op: str          # '+', '-', '*'
    left: object     # Const | Var
    right: object    # Const | Var

@dataclass
class Declare:
    name: Token
    mutable: bool
    init: object     # Const | Var | BinOp
    first: Token     # the statement's first token

@dataclass
class Assign:
    target: Token
    expr: object     # Const | Var | BinOp
    first: Token

@dataclass
class Exit:
    operand: object  # Const | Var
    first: Token


# ---------------------------------------------------------------------------
# Phase 1: tokens -> AST  (syntax errors only)
#
# One statement per line, so each line of tokens is parsed on its own:
#   declaration:  i32 [mut] name { expr }
#   assignment:   name := expr
#   exit:         exit operand
#   expr:         operand [ (+ | - | *) operand ]
#   operand:      number | name
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase 2: AST -> IR  (semantic errors: declaration before use, mut)
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    ptr: object      # the alloca
    mutable: bool
    declared: Token


def lookup(symbols, token):
    if token.text not in symbols:
        raise error_at(token, f"variable '{token.text}' is used before its declaration")
    return symbols[token.text]


def emit_operand(builder, symbols, node):
    """Turn a Const or Var into an IR value."""
    if isinstance(node, Const):
        return ir.Constant(I32, node.value)
    return builder.load(lookup(symbols, node.token).ptr)


def emit_expr(builder, symbols, expr):
    if isinstance(expr, BinOp):
        lhs = emit_operand(builder, symbols, expr.left)
        rhs = emit_operand(builder, symbols, expr.right)
        op = {"+": builder.add, "-": builder.sub, "*": builder.mul}[expr.op]
        return op(lhs, rhs)
    return emit_operand(builder, symbols, expr)


def codegen(stmts, end):
    module = ir.Module(name="practice2")
    module.triple = llvm.get_default_triple()

    main = ir.Function(module, ir.FunctionType(I32, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))

    printf = ir.Function(
        module,
        ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
        name="printf",
    )

    text = b"Program exit with result %d\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(I8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(I8, len(text)), bytearray(text))

    symbols = {}          # name -> Symbol
    seen_exit = False

    for stmt in stmts:
        if seen_exit:
            raise error_at(stmt.first, "'exit' must be the last statement")

        if isinstance(stmt, Declare):
            name = stmt.name.text
            if name in symbols:
                prev = symbols[name].declared
                raise error_at(stmt.name, f"variable '{name}' is already declared at line {prev.line}:{prev.col}")
            # The initialiser is evaluated before the name exists, so `i32 t{t}` is an error.
            value = emit_expr(builder, symbols, stmt.init)
            ptr = builder.alloca(I32, name=name)
            builder.store(value, ptr)
            symbols[name] = Symbol(ptr, stmt.mutable, stmt.name)

        elif isinstance(stmt, Assign):
            symbol = lookup(symbols, stmt.target)
            if not symbol.mutable:
                raise error_at(stmt.target, f"cannot assign to '{stmt.target.text}': it is not mut")
            builder.store(emit_expr(builder, symbols, stmt.expr), symbol.ptr)

        elif isinstance(stmt, Exit):
            value = emit_operand(builder, symbols, stmt.operand)
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), value])
            builder.ret(ir.Constant(I32, 0))
            seen_exit = True

    if not seen_exit:
        raise CompileError(*end, "program has no exit statement")

    return module


# ---------------------------------------------------------------------------

def report(e):
    print(f"compilation error: line {e.line}:{e.col}: {e.msg}", file=sys.stderr)
    sys.exit(1)


def read_source(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        sys.exit(2)


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        print("usage: python3 compiler.py <source> <output.ll>\n"
              "       python3 compiler.py --tokens <source>", file=sys.stderr)
        sys.exit(2)

    if args[0] == "--tokens":                # debug: print the token stream, one source line per row
        data = read_source(args[1])
        try:
            token_lines = lex(data)
        except CompileError as e:
            report(e)
        for tokens in token_lines:
            print("  ".join(str(t) for t in tokens))
        return

    src_path, out_path = args
    data = read_source(src_path)
    try:
        token_lines = lex(data)
        module = codegen(parse(token_lines), end_of_input(token_lines))
    except CompileError as e:
        report(e)

    with open(out_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
