"""Phase 3: a walk over the checked AST that emits the IR

Nothing calls the builder until the whole tree stands and the semantic pass has approved
it; then this visitor walks it. One method per node kind: statement nodes return nothing,
expression nodes return the value the builder produced for them, so an operation asks its
children for their values first and then emits add, sub or mul -- operands before the
operation, bottom-up.

There are no checks here. Declared before use, declared once, mut before ':=' and every
type rule live in src/semantic.py, which ran first; this walk trusts the tree and reads
the two fields that pass left on it: `node.type` on expressions, `node.decl` on names.

Types are explicit in the IR: an i32 is an i32, an i64 is an i64, a bool is an i1. The one
implicit conversion of the language -- i32 widening into i64 -- becomes an explicit sext,
emitted by coerce() at exactly the five places the checker allows it: an initialiser, an
assignment, the operands of + - *, the operands of == !=, and the exit value.
"""

from llvmlite import ir
import llvmlite.binding as llvm

from .semantic import wider

I1, I8, I32, I64 = ir.IntType(1), ir.IntType(8), ir.IntType(32), ir.IntType(64)

LLVM_TYPE = {"i32": I32, "i64": I64, "bool": I1}

I8_PTR = ir.PointerType(I8)


class CodeGen:
    """Builds a module with a single main function from a checked ProgramNode."""

    def __init__(self):
        self.module = ir.Module(name="practice4")
        self.module.triple = llvm.get_default_triple()

        main = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.builder = ir.IRBuilder(main.append_basic_block("entry"))

        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [I8_PTR], var_arg=True),
            name="printf",
        )

        # An integer is printed as %lld after widening to i64; a bool is printed as %s
        # with one of the two words chosen by a select.
        self.fmt_int = self.global_string("fmt_int", b"Program exit with result %lld\n\0")
        self.fmt_str = self.global_string("fmt_str", b"Program exit with result %s\n\0")
        self.word_true = self.global_string("word_true", b"true\0")
        self.word_false = self.global_string("word_false", b"false\0")

        self.slots = {}            # name -> the alloca holding it

    def global_string(self, name, text):
        array = ir.ArrayType(I8, len(text))
        variable = ir.GlobalVariable(self.module, array, name=name)
        variable.linkage, variable.global_constant = "private", True
        variable.initializer = ir.Constant(array, bytearray(text))
        return variable

    def run(self, program):
        program.accept(self)
        return self.module

    def coerce(self, value, have, want):
        """The language's one implicit conversion, made explicit."""
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    # -- statements: emit IR, return nothing --------------------------------

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        value = self.coerce(node.init.accept(self), node.init.type, node.type_name)
        ptr = self.builder.alloca(LLVM_TYPE[node.type_name], name=node.name)
        self.builder.store(value, ptr)
        self.slots[node.name] = ptr

    def visit_assign(self, node):
        want = node.decl.type_name
        value = self.coerce(node.value.accept(self), node.value.type, want)
        self.builder.store(value, self.slots[node.name])

    def visit_exit(self, node):
        value = node.value.accept(self)
        if node.value.type == "bool":
            word = self.builder.select(value,
                                       self.builder.bitcast(self.word_true, I8_PTR),
                                       self.builder.bitcast(self.word_false, I8_PTR),
                                       name="word")
            self.builder.call(self.printf, [self.builder.bitcast(self.fmt_str, I8_PTR), word])
        else:
            value = self.coerce(value, node.value.type, "i64")
            self.builder.call(self.printf, [self.builder.bitcast(self.fmt_int, I8_PTR), value])
        self.builder.ret(ir.Constant(I32, 0))

    # -- expressions: return the value the builder produced -----------------

    def visit_binop(self, node):
        lhs, rhs = node.left.accept(self), node.right.accept(self)

        if node.op in ("+", "-", "*"):
            # both operands in the result type: add i32 %a, i64 %b does not exist
            lhs = self.coerce(lhs, node.left.type, node.type)
            rhs = self.coerce(rhs, node.right.type, node.type)
            op = {"+": self.builder.add, "-": self.builder.sub, "*": self.builder.mul}[node.op]
            return op(lhs, rhs)

        # == or !=: icmp needs both operands at the same width
        if node.left.type in ("i32", "i64"):
            width = wider(node.left.type, node.right.type)
            lhs = self.coerce(lhs, node.left.type, width)
            rhs = self.coerce(rhs, node.right.type, width)
        return self.builder.icmp_signed(node.op, lhs, rhs, name="cmp")

    def visit_var(self, node):
        return self.builder.load(self.slots[node.name])

    def visit_bool(self, node):
        return ir.Constant(I1, 1 if node.value else 0)

    def visit_const(self, node):
        return ir.Constant(LLVM_TYPE[node.type], node.value)


def codegen(program):
    return CodeGen().run(program)
