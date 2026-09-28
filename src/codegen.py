"""Phase 2: a walk over the AST that emits the IR  (semantic errors)

Nothing calls the builder until the whole tree stands; then this visitor walks it.
One method per node kind: statement nodes return nothing, expression nodes return the
value the builder produced for them, so an operation asks its children for their values
first and then emits add, sub or mul -- operands before the operation, bottom-up.

The three semantic checks live here, as they always did: declared before use, declared
once, and mut before ':='. Their positions now come from the nodes, not from tokens.
"""

from dataclasses import dataclass

from llvmlite import ir
import llvmlite.binding as llvm

from .errors import CompileError, error_at

I32, I8 = ir.IntType(32), ir.IntType(8)


@dataclass
class Symbol:
    ptr: object      # the alloca
    mutable: bool
    line: int        # where it was declared
    col: int


class CodeGen:
    """Builds a module with a single main function from a ProgramNode."""

    def __init__(self):
        self.module = ir.Module(name="practice3")
        self.module.triple = llvm.get_default_triple()

        main = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.builder = ir.IRBuilder(main.append_basic_block("entry"))

        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
            name="printf",
        )

        text = b"Program exit with result %d\n\0"
        self.fmt = ir.GlobalVariable(self.module, ir.ArrayType(I8, len(text)), name="fmt")
        self.fmt.linkage, self.fmt.global_constant = "private", True
        self.fmt.initializer = ir.Constant(ir.ArrayType(I8, len(text)), bytearray(text))

        self.symbols = {}          # name -> Symbol

    def run(self, program):
        program.accept(self)
        return self.module

    def lookup(self, node, name):
        if name not in self.symbols:
            raise error_at(node, f"variable '{name}' is used before its declaration")
        return self.symbols[name]

    # -- statements: emit IR, return nothing --------------------------------

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        if node.type_name != "i32":        # TODO (Task 3): i64 allocas, i1 for bool, sext
            raise error_at(node, f"type '{node.type_name}' is not supported by the code generator yet")
        if node.name in self.symbols:
            prev = self.symbols[node.name]
            raise error_at(node, f"variable '{node.name}' is already declared at line {prev.line}:{prev.col}")
        # The initialiser is evaluated before the name exists, so `i32 t{t}` is an error.
        value = node.init.accept(self)
        ptr = self.builder.alloca(I32, name=node.name)
        self.builder.store(value, ptr)
        self.symbols[node.name] = Symbol(ptr, node.mutable, node.line, node.col)

    def visit_assign(self, node):
        symbol = self.lookup(node, node.name)
        if not symbol.mutable:
            raise error_at(node, f"cannot assign to '{node.name}': it is not mut")
        self.builder.store(node.value.accept(self), symbol.ptr)

    def visit_exit(self, node):
        value = node.value.accept(self)
        self.builder.call(self.printf, [self.builder.bitcast(self.fmt, ir.PointerType(I8)), value])
        self.builder.ret(ir.Constant(I32, 0))

    # -- expressions: return the value the builder produced -----------------

    def visit_binop(self, node):
        lhs = node.left.accept(self)
        rhs = node.right.accept(self)
        op = {"+": self.builder.add, "-": self.builder.sub, "*": self.builder.mul}[node.op]
        return op(lhs, rhs)

    def visit_bool(self, node):
        # TODO (Task 3): a bool is an i1; comparisons become icmp.
        raise error_at(node, "booleans are not supported by the code generator yet")

    def visit_var(self, node):
        return self.builder.load(self.lookup(node, node.name).ptr)

    def visit_const(self, node):
        return ir.Constant(I32, node.value)


def codegen(program):
    return CodeGen().run(program)
