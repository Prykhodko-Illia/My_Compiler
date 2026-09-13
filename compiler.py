import re
import sys
from dataclasses import dataclass
from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)
RESERVED = {"int", "exit"}


class CompileError(Exception):
    """Carries a source line number so we can print `line N: ...`."""
    def __init__(self, lineno, msg):
        super().__init__(msg)
        self.lineno = lineno
        self.msg = msg


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

def main():
    if len(sys.argv) != 3:
        print("usage: python3 compiler.py <source> <output.ll>", file=sys.stderr)
        sys.exit(2)

    src_path, out_path = sys.argv[1], sys.argv[2]
    with open(src_path) as f:
        lines = f.readlines()

    try:
        module = codegen(parse(lines))
    except CompileError as e:
        print(f"compilation error: line {e.lineno}: {e.msg}", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
