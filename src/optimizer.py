"""Phase 2.5: removing what nobody reads  (--remove-unused)

The first pass that drops parts of the program. It runs on the tree the semantic pass has
accepted and annotated, before the code generator: an invalid program never reaches it,
and every name in the tree already points at its declaration through `decl`.

A variable is used where it is read: in a value, a condition, an argument, a print, an
exit, at the start of a field chain, as the object of a member call or of a call of an
object, in a capture list (the lambda's declaration reads it). An assignment to it is not
a read -- y := 5 on a y nobody reads is as dead as the declaration, and goes with it.

A call may print and read() reads the input, so a value that contains either is kept: a
declaration with one in its values counts as read by itself, and so does the declaration
behind an assignment with one in its value -- the assignment then stays, and so must the
slot it writes.

The reads are counted per declaration, not per name: two x in two scopes are two
DeclNodes, and the checker already knows which one each name means. A declaration with no
reads is removed from its block with every assignment to it. That can leave another one
with no reads -- y initialised from x was x's only reader -- so the pass counts again on
the new tree, and stops when a round removes nothing. Every round that does not stop
removes at least one declaration, so it stops.

Declarations are removed wherever a block holds them: at the top level, in if and while
blocks, in function, member function and lambda bodies. Parameters, fields and lambdas
stay: a parameter is part of its function's signature, a field part of its struct's type.
A block left empty stays too -- its if still evaluates its condition.

A variable that only feeds itself, k := k + 1 with no other reader, survives: the right
side reads it. Counting cannot see that the read only serves the write.
"""

from .ast_nodes import AssignNode, CallNode, DeclNode, IfNode, LambdaNode, MemberCallNode, ReadNode, WhileNode


def has_call_or_read(expr):
    """Does evaluating `expr` call a function or read the input -- may it be observed?"""
    if isinstance(expr, (CallNode, MemberCallNode, ReadNode)):
        return True
    return any(has_call_or_read(child) for child in expr.children())


class UseCounter:
    """One walk over the whole tree: for every DeclNode, the number of reads that resolve
    to it. Keys are id(decl), so two declarations named x are two keys."""

    def __init__(self):
        self.reads = {}                   # id(DeclNode) -> count

    def read(self, decl):
        self.reads[id(decl)] = self.reads.get(id(decl), 0) + 1

    # -- the program and its functions ---------------------------------------

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        for fn in node.fns:
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_struct(self, node):
        for method in node.methods:
            method.accept(self)

    def visit_fn(self, node):
        node.body.accept(self)

    visit_member_fn = visit_fn

    # -- statements ------------------------------------------------------------

    def visit_decl(self, node):
        self.reads.setdefault(id(node), 0)
        if any(has_call_or_read(value) for value in node.values):
            self.read(node)               # kept: its value may print or read
        for value in node.values:
            value.accept(self)

    def visit_lambda(self, node):
        # Not a declaration the pass may remove: only what it reads counts. Its captures
        # are the values of its variable, and its body is the call of its struct.
        for value in node.decl.values:
            value.accept(self)
        node.body.accept(self)

    def visit_assign(self, node):
        if has_call_or_read(node.value):  # the target is not a read, unless the value may print
            self.read(node.decl)
        node.value.accept(self)

    def visit_if(self, node):
        node.condition.accept(self)
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        node.condition.accept(self)
        node.body.accept(self)

    def visit_print(self, node):
        node.value.accept(self)

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_exit(self, node):
        node.value.accept(self)

    # -- expressions -----------------------------------------------------------

    def visit_var(self, node):
        self.read(node.decl)              # a DeclNode, a ParamNode or a ThisParam

    def visit_field(self, node):
        self.read(node.decl)              # p.x reads p

    def visit_call(self, node):
        if node.callable:                 # c(5): a read of c, then the arguments
            node.callable.accept(self)
            return
        for arg in node.args:
            arg.accept(self)

    def visit_member_call(self, node):
        if node.callable:                 # s.scale(7): the CallableCallNode reads s.scale
            node.callable.accept(self)
            return
        node.target.accept(self)          # p.sum() reads p; a CallableCallNode's target is the object
        for arg in node.args:
            arg.accept(self)

    def visit_result_field(self, node):
        node.value.accept(self)

    def visit_binop(self, node):
        node.left.accept(self)
        node.right.accept(self)

    def visit_not(self, node):
        node.operand.accept(self)

    def visit_init(self, node):
        for value in node.values:
            value.accept(self)

    def visit_read(self, node):
        pass

    def visit_string(self, node):
        pass

    def visit_bool(self, node):
        pass

    def visit_const(self, node):
        pass


class Pruner:
    """Rebuilds every statement list without the dead declarations and the assignments to
    them. The lists are rebuilt, never deleted from while they are walked."""

    def __init__(self, dead):
        self.dead = dead                  # {id(DeclNode)} with no reads

    def prune(self, program):
        for struct in program.structs:
            for method in struct.methods:
                self.prune_block(method.body)
        for fn in program.fns:
            self.prune_block(fn.body)
        program.statements = self.prune_statements(program.statements)

    def prune_block(self, block):
        block.statements = self.prune_statements(block.statements)

    def prune_statements(self, statements):
        kept = []
        for stmt in statements:
            if isinstance(stmt, DeclNode) and id(stmt) in self.dead:
                continue
            if isinstance(stmt, AssignNode) and id(stmt.decl) in self.dead:   # x := ..., p.x := ...
                continue
            if isinstance(stmt, IfNode):
                self.prune_block(stmt.then_block)
                if stmt.else_block:
                    self.prune_block(stmt.else_block)
            elif isinstance(stmt, WhileNode):
                self.prune_block(stmt.body)
            elif isinstance(stmt, LambdaNode):
                self.prune_block(stmt.body)
            kept.append(stmt)
        return kept


def remove_unused(program):
    """Remove every declaration nobody reads, round after round, until a round removes
    nothing; return the same tree."""
    while True:
        counter = UseCounter()
        program.accept(counter)
        dead = {decl for decl, count in counter.reads.items() if count == 0}
        if not dead:
            return program
        Pruner(dead).prune(program)
