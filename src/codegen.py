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

Every function of the program becomes a function of the module, named with a prefix --
fn_check, fn_fib -- so a function called main or printf cannot clash with the runtime's.
All of them are declared before any body is built, so a call can name a function whose
body does not exist yet (itself, or one below). Each has its own entry block; its
parameters are stored into slots there, like declarations, so a body reads a parameter
with a load like any variable. exit in a function is ret of its value; exit at the top
level is the printf and ret 0 of main, as before. The top-level statements are main.

A struct is a named type, %"Point" = type {i32, i32}, and an object is one alloca of it.
A field is reached with gep: index 0 steps through the pointer to the object itself, then
one index per link of the chain, so c.center.x is one gep with indices 0, 0, 0. The
indices come from the semantic pass; nothing here looks a field up by name. A whole
object moves as one value -- a load of %"Point" and a store of it -- which is how
Point q{p} copies and how an object lands in a struct-typed field.

A member function is a function of the module named after its struct and itself,
Point_shift, whose first parameter is a pointer to the object it is called on -- `this`.
The object is passed by address, so the function changes the caller's object, not a copy.
A pointer to an object is exactly what the alloca of a struct variable is, so `this` needs
no slot of its own: it stands in the slot table for the function's ThisParam, and a field
x in the body -- indices [0] on this -- is the same gep as p.x on a variable. A call
passes the address of its object: the variable's alloca, or a gep into it for
c.center.d2(); a member function calling one of its own struct's passes `this` on.

print is printf, the same declaration exit uses, with a format by type: "%d\n" for an
i32, "%lld\n" for an i64, "%s\n" for a bool (a select between the global strings "true"
and "false") and for a string literal, whose text goes in as the argument so a % in it
stays a %. A struct is printed by walking its field table -- printf("Point{"), then for
every field a gep, a load and a printf with a format like ", y: %d", a struct field the
same way one level down, then printf("}") -- so a new struct needs no new code here. An
object that is not in a variable, a call result, is stored into a slot first. Every
format and text is a global constant, one per distinct text. read() is scanf("%d") into
a slot in the entry block, then a load of it.

An inner {} -- the {10, 20} of Circle c{{10, 20}, 5} -- is not an object in memory but one
value of the struct type, built in registers: it starts as undef and each field goes in
with insertvalue, by index. The outer {} still writes the object's fields with gep and
store; an inner one is just a value that lands in one of them.

Every alloca goes to the entry block, wherever the builder is when the declaration is met:
a slot made inside then would not exist on the else path, a slot made inside a loop body
would take more stack on every pass, and LLVM promotes only entry-block allocas to
registers (opt -passes=mem2reg).
"""

from llvmlite import ir
import llvmlite.binding as llvm

from .ast_nodes import FieldNode, MemberFnNode, StringNode, VarNode, chain
from .semantic import wider

I1, I8, I32, I64 = ir.IntType(1), ir.IntType(8), ir.IntType(32), ir.IntType(64)

LLVM_TYPE = {"i32": I32, "i64": I64, "bool": I1}     # the structs join it per module

I8_PTR = ir.PointerType(I8)

FORMAT = {"i32": b"%d", "i64": b"%lld"}             # what print uses for each integer type

FN_PREFIX = "fn_"                 # fn main := ... becomes @fn_main, not a second @main


class CodeGen:
    """Builds a module from a checked ProgramNode: one function per fn, and main."""

    def __init__(self):
        # A context of our own: named struct types live in the context, and the global one
        # would keep a %"Point" from an earlier module in the same process.
        self.context = ir.Context()
        self.module = ir.Module(name="practice7", context=self.context)
        self.module.triple = llvm.get_default_triple()
        self.types = dict(LLVM_TYPE)      # type name -> LLVM type: built-ins, then each struct
        self.structs = {}                 # struct name -> StructNode, for its fields' types

        # The function being built. begin_function() sets all four; each function has its
        # own entry block and its own run of allocas at its top.
        self.function = self.entry = self.builder = None
        self.last_slot = None      # the last alloca in entry: the next one goes after it
        self.current_fn = None     # the FnNode being built, or None for main

        self.fns = {}              # FnNode / MemberFnNode -> its ir.Function, every one before any body

        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [I8_PTR], var_arg=True),
            name="printf",
        )
        self.scanf = ir.Function(
            self.module,
            ir.FunctionType(I32, [I8_PTR], var_arg=True),
            name="scanf",
        )

        # An integer is printed as %lld after widening to i64; a bool is printed as %s
        # with one of the two words chosen by a select.
        self.fmt_int = self.global_string("fmt_int", b"Program exit with result %lld\n\0")
        self.fmt_str = self.global_string("fmt_str", b"Program exit with result %s\n\0")
        self.word_true = self.global_string("word_true", b"true\0")
        self.word_false = self.global_string("word_false", b"false\0")
        self.texts = {b"true": self.word_true, b"false": self.word_false}  # text -> its global

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

    def text(self, data):
        """An i8* to the global constant holding `data` and a NUL: one global per distinct
        text, however many prints use it."""
        if data not in self.texts:
            self.texts[data] = self.global_string(self.module.get_unique_name("str"), data + b"\0")
        return self.builder.bitcast(self.texts[data], I8_PTR)

    def printf_call(self, fmt, *values):
        self.builder.call(self.printf, [self.text(fmt), *values])

    def word(self, value):
        """The word for an i1: a select between the global strings "true" and "false"."""
        return self.builder.select(value, self.text(b"true"), self.text(b"false"), name="word")

    def run(self, program):
        program.accept(self)
        return self.module

    def begin_function(self, function, fn_node):
        """Point the builder at a fresh function: its own entry block, its own slots."""
        self.function, self.current_fn = function, fn_node
        self.entry = function.append_basic_block("entry")
        self.builder = ir.IRBuilder(self.entry)
        self.last_slot = None

    def alloca_in_entry(self, llvm_type, name):
        """A slot at the top of the entry block, then back to where we were."""
        here = self.builder.block
        if self.last_slot is None:
            self.builder.position_at_start(self.entry)
        else:
            self.builder.position_after(self.last_slot)          # declaration order
        self.last_slot = self.builder.alloca(llvm_type, name=name)
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
        for fn in node.fns:                                      # every signature first...
            self.fns[fn] = self.declare(fn, [], FN_PREFIX + fn.name)
        for struct in node.structs:
            this_type = self.types[struct.name].as_pointer()     # the object, by address
            for method in struct.methods:
                # Names may contain '_': A.b_c and A_b.c both make A_b_c, and struct fmt's
                # int would be the global fmt_int. The first one keeps the name, a later
                # one gets a suffix -- the program's own functions are declared above.
                name = self.module.get_unique_name(f"{struct.name}_{method.name}")
                self.fns[method] = self.declare(method, [this_type], name)
        for struct in node.structs:                              # ...then the bodies
            for method in struct.methods:
                method.accept(self)
        for fn in node.fns:
            fn.accept(self)

        main = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.begin_function(main, None)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def declare(self, fn, leading, name):
        """The ir.Function for `fn`: `leading` parameter types, then one per parameter."""
        fn_type = ir.FunctionType(self.types[fn.result_type],
                                  [*leading, *(self.types[param.type_name] for param in fn.params)])
        return ir.Function(self.module, fn_type, name=name)

    def visit_fn(self, node):
        self.build_body(node, self.fns[node].args)

    def visit_member_fn(self, node):
        this, *args = self.fns[node].args
        this.name = "this"
        self.slots[node.this] = this      # already a pointer to the object: no slot of its own
        self.build_body(node, args)

    def build_body(self, node, args):
        self.begin_function(self.fns[node], node)
        for param, arg in zip(node.params, args):               # a parameter is a copy in a slot
            arg.name = param.name
            self.slots[param] = self.alloca_in_entry(self.types[param.type_name], param.name)
            self.builder.store(arg, self.slots[param])
        node.body.accept(self)                                   # its last line is an exit: a ret

    def visit_struct(self, node):
        struct_type = self.context.get_identified_type(node.name)
        # Every field type is built in or a struct above, so it is already in self.types.
        struct_type.set_body(*(self.types[field.type_name] for field in node.fields))
        self.types[node.name] = struct_type
        self.structs[node.name] = node

    def visit_decl(self, node):
        ptr = self.alloca_in_entry(self.types[node.type_name], node.name)
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
        # A ret wherever the exit stands -- in entry, in an arm or in a merge. The ret
        # terminates the current block, so an if around it adds no br.
        value = node.value.accept(self)
        if self.current_fn is not None:                          # in a function: its result
            self.builder.ret(self.coerce(value, node.value.type, self.current_fn.result_type))
            return
        # at the top level: print, and end the program
        if node.value.type == "bool":
            self.builder.call(self.printf, [self.builder.bitcast(self.fmt_str, I8_PTR), self.word(value)])
        else:
            value = self.coerce(value, node.value.type, "i64")
            self.builder.call(self.printf, [self.builder.bitcast(self.fmt_int, I8_PTR), value])
        self.builder.ret(ir.Constant(I32, 0))

    def visit_print(self, node):
        value = node.value
        if isinstance(value, StringNode):                        # its text as written: a % stays a %
            self.printf_call(b"%s\n", self.text(value.text.encode("latin-1")))
        elif value.type in self.structs:                         # Point{x: 3, y: 4}
            self.print_struct(self.object_address(value), value.type)
            self.printf_call(b"\n")
        else:
            self.print_scalar(value.accept(self), value.type, b"", b"\n")

    def print_scalar(self, value, type_name, before, after):
        """An i32, an i64 or a bool between `before` and `after`, in one printf."""
        if type_name == "bool":
            self.printf_call(before + b"%s" + after, self.word(value))
        else:
            self.printf_call(before + FORMAT[type_name] + after, value)

    def print_struct(self, ptr, type_name):
        """The object at `ptr`: its type's name, then each field in declaration order."""
        self.printf_call(type_name.encode() + b"{")
        for field in self.structs[type_name].fields:
            before = (b", " if field.index else b"") + field.name.encode() + b": "
            field_ptr = self.gep(ptr, [field.index], name=field.name)
            if field.type_name in self.structs:                  # one level down, the same way
                self.printf_call(before)
                self.print_struct(field_ptr, field.type_name)
            else:
                self.print_scalar(self.builder.load(field_ptr), field.type_name, before, b"")
        self.printf_call(b"}")

    def object_address(self, value):
        """Where a struct value lives: a variable or a field chain has a place already; any
        other value -- a call result -- is stored into a slot of its own first."""
        if isinstance(value, (VarNode, FieldNode)):
            return self.address_of(value)
        slot = self.alloca_in_entry(self.types[value.type], "tmp")
        self.builder.store(value.accept(self), slot)
        return slot

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
        return self.builder.load(self.address_of(node))

    def visit_field(self, node):
        return self.builder.load(self.address_of(node))

    def address_of(self, node):
        """Where a VarNode or a FieldNode lives: the variable's slot, then a gep along the
        chain. A field named in a member function is a gep on `this`."""
        ptr = self.slots[node.decl]
        if node.indices:
            ptr = self.gep(ptr, node.indices, name=chain(node.name, getattr(node, "links", [])))
        return ptr

    def visit_call(self, node):
        this = []
        if isinstance(node.fn, MemberFnNode):                    # d2() in Point: on the same object
            this = [self.slots[self.current_fn.this]]
        return self.builder.call(self.fns[node.fn], [*this, *self.args(node)], name=node.name)

    def visit_member_call(self, node):
        this = self.address_of(node.target)                      # the object, by address
        return self.builder.call(self.fns[node.fn], [this, *self.args(node)], name=node.name)

    def args(self, node):
        return [self.coerce(arg.accept(self), arg.type, param.type_name)    # an i32 into an i64 parameter
                for param, arg in zip(node.fn.params, node.args)]

    def visit_init(self, node):
        if node.copy:                                            # {p}: the whole object
            return node.values[0].accept(self)
        struct = self.structs[node.type]
        value = ir.Constant(self.types[node.type], ir.Undefined) # nothing in it yet
        for field, part in zip(struct.fields, node.values):     # an InitNode part recurses here
            part_value = self.coerce(part.accept(self), part.type, field.type_name)
            value = self.builder.insert_value(value, part_value, field.index)
        return value

    def visit_read(self, node):
        slot = self.alloca_in_entry(I32, "input")                # scanf writes here
        self.builder.call(self.scanf, [self.text(b"%d"), slot])
        return self.builder.load(slot, name="read")

    def visit_bool(self, node):
        return ir.Constant(I1, 1 if node.value else 0)

    def visit_const(self, node):
        return ir.Constant(LLVM_TYPE[node.type], node.value)


def codegen(program):
    return CodeGen().run(program)
