import re
import sys
from dataclasses import dataclass
from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)
RESERVED = {"int", "exit"}


class CompileError(Exception):
    """Carries a source position so we can print `line N:C: ...` (or `line N: ...`)."""
    def __init__(self, lineno, msg, col=None):
        super().__init__(msg)
        self.lineno = lineno
        self.col = col
        self.msg = msg


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
                    brace = open_braces[-1]
                    raise CompileError(brace.line, "'{' is not closed before the end of the line", brace.col)
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
                raise CompileError(line, "unexpected byte '=': assignment is ':='", col)
            else:
                raise CompileError(line, f"unexpected byte {show_byte(b)}", col)

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
                raise CompileError(line, f"invalid number {data[start:i + 1].decode()!r}: a letter inside a number", start_col)
            else:
                tokens.append(Token("constant", data[start:i].decode(), line, start_col, "numeric"))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "COLON":
            if b == EQUALS:
                tokens.append(Token("operator", ":=", line, start_col, "assign"))
                state = "START"
            else:
                raise CompileError(line, "':' must be followed by '='", start_col)

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
# Every statement carries the source line number for error messages.
# ---------------------------------------------------------------------------

@dataclass
class Const:
    value: int

@dataclass
class Var:
    name: str

@dataclass
class BinOp:
    op: str          # '+', '-', '*'
    left: object     # Const | Var
    right: object    # Const | Var

@dataclass
class Declare:
    name: str
    lineno: int

@dataclass
class Assign:
    target: str
    expr: object     # Const | Var | BinOp
    lineno: int

@dataclass
class Exit:
    name: str
    lineno: int


# ---------------------------------------------------------------------------
# Phase 1: parse text -> AST  (syntax errors only)
# ---------------------------------------------------------------------------

def parse_name(token, lineno):
    """A bare identifier: letters/digits/_, not starting with a digit, not reserved."""
    token = token.strip()
    if not token.isidentifier() or token in RESERVED:
        raise CompileError(lineno, f"bad variable name {token!r}")
    return token


def parse_operand(token, lineno):
    """One operand of an expression: an integer constant or a variable."""
    token = token.strip()
    if not token:
        raise CompileError(lineno, "missing operand")
    if re.fullmatch(r"\d+", token):
        return Const(int(token))
    return Var(parse_name(token, lineno))


def parse_expr(rhs, lineno):
    """Right-hand side of `:=`: a single operand, or `operand op operand`."""
    # Split on a single +, - or * with optional surrounding spaces.
    parts = re.split(r"\s*([+\-*])\s*", rhs.strip())
    if len(parts) == 1:                      # just an operand
        return parse_operand(parts[0], lineno)
    if len(parts) == 3:                      # operand op operand
        left, op, right = parts
        return BinOp(op, parse_operand(left, lineno), parse_operand(right, lineno))
    raise CompileError(lineno, f"expected at most one operator in {rhs.strip()!r}")


def parse_line(line, lineno):
    line = line.strip()

    if line.startswith("int "):
        return Declare(parse_name(line[4:], lineno), lineno)

    if line.startswith("exit "):
        return Exit(parse_name(line[5:], lineno), lineno)

    if ":=" in line:
        lhs, rhs = line.split(":=", 1)
        return Assign(parse_name(lhs, lineno), parse_expr(rhs, lineno), lineno)

    raise CompileError(lineno, f"cannot parse: {line!r}")


def parse(lines):
    """Return a list of statement nodes, skipping blank lines."""
    stmts = []
    for lineno, raw in enumerate(lines, start=1):
        if raw.strip():
            stmts.append(parse_line(raw, lineno))
    return stmts


# ---------------------------------------------------------------------------
# Phase 2: AST -> IR  (semantic errors: declared/undeclared)
# ---------------------------------------------------------------------------

def emit_operand(builder, symbols, node, lineno):
    """Turn a Const or Var into an IR value."""
    if isinstance(node, Const):
        return ir.Constant(I32, node.value)
    # Var
    if node.name not in symbols:
        raise CompileError(lineno, f"undeclared variable {node.name!r}")
    return builder.load(symbols[node.name])


def emit_expr(builder, symbols, expr, lineno):
    if isinstance(expr, BinOp):
        lhs = emit_operand(builder, symbols, expr.left, lineno)
        rhs = emit_operand(builder, symbols, expr.right, lineno)
        op = {"+": builder.add, "-": builder.sub, "*": builder.mul}[expr.op]
        return op(lhs, rhs)
    return emit_operand(builder, symbols, expr, lineno)


def codegen(stmts):
    module = ir.Module(name="practice1")
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

    symbols = {}          # name -> alloca pointer
    seen_exit = False

    for stmt in stmts:
        if seen_exit:
            raise CompileError(stmt.lineno, "statement after exit")

        if isinstance(stmt, Declare):
            if stmt.name in symbols:
                raise CompileError(stmt.lineno, f"redeclared variable {stmt.name!r}")
            symbols[stmt.name] = builder.alloca(I32, name=stmt.name)

        elif isinstance(stmt, Assign):
            if stmt.target not in symbols:
                raise CompileError(stmt.lineno, f"undeclared variable {stmt.target!r}")
            value = emit_expr(builder, symbols, stmt.expr, stmt.lineno)
            builder.store(value, symbols[stmt.target])

        elif isinstance(stmt, Exit):
            if stmt.name not in symbols:
                raise CompileError(stmt.lineno, f"undeclared variable {stmt.name!r}")
            builder.call(printf, [
                builder.bitcast(fmt, ir.PointerType(I8)),
                builder.load(symbols[stmt.name]),
            ])
            builder.ret(ir.Constant(I32, 0))
            seen_exit = True

    if not seen_exit:
        last = stmts[-1].lineno if stmts else 1
        raise CompileError(last, "program has no exit statement")

    return module


# ---------------------------------------------------------------------------

def report(e):
    where = f"{e.lineno}:{e.col}" if e.col is not None else f"{e.lineno}"
    print(f"compilation error: line {where}: {e.msg}", file=sys.stderr)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        print("usage: python3 compiler.py <source> <output.ll>\n"
              "       python3 compiler.py --tokens <source>", file=sys.stderr)
        sys.exit(2)

    if args[0] == "--tokens":                # debug: print the token stream, one source line per row
        with open(args[1], "rb") as f:
            data = f.read()
        try:
            token_lines = lex(data)
        except CompileError as e:
            report(e)
        for tokens in token_lines:
            print("  ".join(str(t) for t in tokens))
        return

    src_path, out_path = args
    with open(src_path, "rb") as f:
        data = f.read()

    try:
        lex(data)                            # lexical errors first
        # The statement layer still reads text; Task 2 moves it onto the tokens.
        module = codegen(parse(data.decode().splitlines(keepends=True)))
    except CompileError as e:
        report(e)

    with open(out_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
