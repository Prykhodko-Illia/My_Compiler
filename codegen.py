"""Phase 2: AST -> IR  (semantic errors: declaration before use, mut)"""

from dataclasses import dataclass

from llvmlite import ir
import llvmlite.binding as llvm

from ast_nodes import Assign, BinOp, Const, Declare, Exit
from errors import CompileError, error_at
from lexer import Token

I32, I8 = ir.IntType(32), ir.IntType(8)


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
