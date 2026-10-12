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

A block of the source is not a basic block of the IR. A source block is a scope, and the
semantic pass already resolved every name in it; here it is only a run of statements. A
basic block is a builder position: an if appends then, else (when there is one) and merge
blocks to the function, ends the current block with a cbranch on the i1 of the condition,
and moves the builder from one to the next. Every basic block ends with exactly one
terminator -- br, or the ret of an exit -- and nothing after it.

A while is three blocks: cond, which the loop returns to, body and end. The body ends with
a br back to cond -- the one edge in the IR that points backwards.

A struct is a named type, %"Point" = type {i32, i32}, and an object is one alloca of it.
A field is reached with gep: index 0 steps through the pointer to the object itself, then
one index per link of the chain, so c.center.x is one gep with indices 0, 0, 0. The
indices come from the semantic pass; nothing here looks a field up by name. A whole
object moves as one value -- a load of %"Point" and a store of it -- which is how
Point q{p} copies and how an object lands in a struct-typed field.

Every alloca goes to the entry block, wherever the builder is when the declaration is met:
a slot made inside then would not exist on the else path, a slot made inside a loop body
would take more stack on every pass, and LLVM promotes only entry-block allocas to
registers (opt -passes=mem2reg).
"""

from llvmlite import ir
import llvmlite.binding as llvm

from .ast_nodes import chain
from .semantic import wider

I1, I8, I32, I64 = ir.IntType(1), ir.IntType(8), ir.IntType(32), ir.IntType(64)

LLVM_TYPE = {"i32": I32, "i64": I64, "bool": I1}     # the structs join it per module

I8_PTR = ir.PointerType(I8)


class CodeGen:
    """Builds a module with a single main function from a checked ProgramNode."""

    def __init__(self):
        # A context of our own: named struct types live in the context, and the global one
        # would keep a %"Point" from an earlier module in the same process.
        self.context = ir.Context()
        self.module = ir.Module(name="practice6", context=self.context)
        self.module.triple = llvm.get_default_triple()
        self.types = dict(LLVM_TYPE)      # type name -> LLVM type: built-ins, then each struct
        self.structs = {}                 # struct name -> StructNode, for its fields' types

        self.function = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.entry = self.function.append_basic_block("entry")
        self.builder = ir.IRBuilder(self.entry)
        self.last_slot = None      # the last alloca in entry: the next one goes after it

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

        # Keyed by the DeclNode, not by the name: two variables in different scopes may
        # share a name, and the tree already says which one is meant. Shadowing costs
        # nothing here: two declarations, two slots.
        self.slots = {}            # DeclNode -> the alloca holding that variable

    def global_string(self, name, text):
        array = ir.ArrayType(I8, len(text))
        variable = ir.GlobalVariable(self.module, array, name=name)
        variable.linkage, variable.global_constant = "private", True
        variable.initializer = ir.Constant(array, bytearray(text))
        return variable

    def run(self, program):
        program.accept(self)
        return self.module

    def alloca_in_entry(self, decl):
        """A slot for `decl` at the top of the entry block, then back to where we were."""
        here = self.builder.block
        if self.last_slot is None:
            self.builder.position_at_start(self.entry)
        else:
            self.builder.position_after(self.last_slot)          # declaration order
        self.last_slot = self.builder.alloca(self.types[decl.type_name], name=decl.name)
        self.builder.position_at_end(here)
        return self.last_slot

    def gep(self, ptr, indices, name):
        """A pointer to a field: 0 to step through `ptr`, then one index per link."""
        return self.builder.gep(ptr, [ir.Constant(I32, i) for i in (0, *indices)], name=name)

    def coerce(self, value, have, want):
        """The language's one implicit conversion, made explicit."""
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    # -- statements: emit IR, return nothing --------------------------------

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_struct(self, node):
        struct_type = self.context.get_identified_type(node.name)
        # Every field type is built in or a struct above, so it is already in self.types.
        struct_type.set_body(*(self.types[field.type_name] for field in node.fields))
        self.types[node.name] = struct_type
        self.structs[node.name] = node

    def visit_decl(self, node):
        ptr = self.alloca_in_entry(node)
        self.slots[node] = ptr
        struct = self.structs.get(node.type_name)
        if struct is None or node.copy:                          # one value: a number or a whole object
            value = node.values[0]
            self.builder.store(self.coerce(value.accept(self), value.type, node.type_name), ptr)
            return
        for field, value in zip(struct.fields, node.values):     # Point p{10, 20}: field by field
            field_ptr = self.gep(ptr, [field.index], name=f"{node.name}.{field.name}")
            self.builder.store(self.coerce(value.accept(self), value.type, field.type_name), field_ptr)

    def visit_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.target_type)
        ptr = self.slots[node.decl]
        if node.indices:                                         # c.center.x := ...
            ptr = self.gep(ptr, node.indices, name=chain(node.name, node.links))
        self.builder.store(value, ptr)                           # a whole object is one store too

    def visit_if(self, node):
        cond = node.condition.accept(self)                       # an i1, in the current block
        then_bb = self.function.append_basic_block("then")
        else_bb = self.function.append_basic_block("else") if node.else_block else None
        merge_bb = self.function.append_basic_block("merge")
        self.builder.cbranch(cond, then_bb, else_bb or merge_bb) # ends the current block

        for block, arm_bb in ((node.then_block, then_bb), (node.else_block, else_bb)):
            if block is None:
                continue
            self.builder.position_at_end(arm_bb)
            block.accept(self)
            # The block the arm ended in, not arm_bb: a nested if moved the builder to its
            # own merge. An arm that exited already has its ret, and a second terminator
            # is not allowed.
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        # What follows the if goes here. When both arms exit, merge has no predecessor;
        # it still gets the statements after the if and stays valid.
        self.builder.position_at_end(merge_bb)

    def visit_while(self, node):
        cond_bb = self.function.append_basic_block("cond")
        body_bb = self.function.append_basic_block("body")
        end_bb = self.function.append_basic_block("end")
        self.builder.branch(cond_bb)                             # a block can't fall through

        self.builder.position_at_end(cond_bb)                    # evaluated on every pass
        self.builder.cbranch(node.condition.accept(self), body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:                 # an exit in the body has its ret
            self.builder.branch(cond_bb)                         # the back edge

        self.builder.position_at_end(end_bb)                     # what follows the while

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_exit(self, node):
        # printf and ret wherever the exit stands -- in entry, in an arm or in a merge. The
        # ret terminates the current block, so an if around it adds no br.
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

    def visit_not(self, node):
        return self.builder.not_(node.operand.accept(self), name="not")   # xor i1 %v, true

    def visit_var(self, node):
        return self.builder.load(self.slots[node.decl])

    def visit_field(self, node):
        ptr = self.gep(self.slots[node.decl], node.indices, name=chain(node.name, node.links))
        return self.builder.load(ptr)

    def visit_bool(self, node):
        return ir.Constant(I1, 1 if node.value else 0)

    def visit_const(self, node):
        return ir.Constant(LLVM_TYPE[node.type], node.value)


def codegen(program):
    return CodeGen().run(program)
