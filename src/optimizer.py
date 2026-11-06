"""Phase 2.5: removing what nobody reads or calls  (--remove-unused)

The first pass that drops parts of the program. It runs on the tree the semantic pass has
accepted and annotated, before the code generator: an invalid program never reaches it,
and every name in the tree already points at its declaration through `decl`.

A variable is used where it is read: in a value, a condition, an argument, a print, an
exit, at the start of a field chain, as the object of a member call or of a call of an
object, in a capture list (the lambda's declaration reads it). An assignment to it is not
a read -- y := 5 on a y nobody reads is as dead as the declaration, and goes with it.

read() reads the input and a call may print, so a value that contains read() or a call
of a function with an effect is kept: a declaration with one in its values counts as read
by itself, and so does the declaration behind an assignment with one in its value -- the
assignment then stays, and so must the slot it writes. A function has an effect when
  * its body prints or reads -- print or read();
  * it is a mutating member function: it changes the object it is called on;
  * it may not return: its body has a while, or it calls itself, directly or through
    other functions -- a call that never returns is observable, the program hangs;
  * it is an extern function: its body is in C, where it may print;
  * it calls a function that has an effect.
The last rule goes round when functions call each other, so the set is a fixpoint: the
first three rules, then every caller of a function in the set, until nothing is added.
A call of an object is the call of its call. A lambda declared in a body runs only when
it is called, so its own body is not part of the body around it. Every other call is
quiet: its value goes with the declaration nobody reads -- and with it, maybe, the only
call of a function, which the next round then removes.

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

Global functions are counted the same way, one level up: every call of one, wherever it
stands -- in the statements, in another function, in a member function, in a lambda
body. An extern function is counted the same way: with no calls its declaration goes.
A function with no calls is removed with its whole body, every declaration and call
inside it in one cut, and the next round no longer sees those calls: the removed body may
have held the only call of another function. Member functions and lambdas stay. Two
functions that call only each other, or one that only calls itself, never reach zero and
stay too -- the reason reference counting cannot free a cycle.
"""

from .ast_nodes import (AssignNode, CallableCallNode, CallNode, DeclNode, ExternFnNode, IfNode, LambdaNode,
                        MemberCallNode, PrintNode, ReadNode, WhileNode)


class Facts:
    """What one function body does by itself: prints or reads, loops, and whom it calls."""

    def __init__(self):
        self.io = False                   # a print or a read()
        self.loops = False                # a while
        self.callees = set()              # {id(FnNode | MemberFnNode)}

    def scan(self, node):
        if isinstance(node, LambdaNode):  # declared here, run where it is called
            for value in node.decl.values:
                self.scan(value)
            return
        if isinstance(node, (CallNode, MemberCallNode)) and node.callable:
            self.scan(node.callable)      # c(5) is c.call(5)
            return
        if isinstance(node, (PrintNode, ReadNode)):
            self.io = True
        elif isinstance(node, WhileNode):
            self.loops = True
        elif isinstance(node, (CallNode, MemberCallNode)):
            self.callees.add(id(node.fn))
            if isinstance(node, CallableCallNode):
                self.scan(node.target)    # not among its children: it may hold a call result
        for child in node.children():
            self.scan(child)


def effectful_functions(program):
    """{id(fn)} of every function, global, member or lambda call, whose call may be
    observed -- the fixpoint of the rules above, on the tree as it is now."""
    structs = [*program.structs, *program.lambda_structs]
    fns = [*program.fns, *(method for struct in structs for method in struct.methods)]
    facts = {}
    for fn in fns:
        facts[id(fn)] = Facts()
        if fn.body is not None:           # an extern has none here
            facts[id(fn)].scan(fn.body)

    def calls_itself(start):
        seen, todo = set(), list(facts[start].callees)
        while todo:
            fn = todo.pop()
            if fn == start:
                return True
            if fn not in seen:
                seen.add(fn)
                todo.extend(facts[fn].callees)
        return False

    effect = {id(fn) for fn in fns
              if facts[id(fn)].io or facts[id(fn)].loops or getattr(fn, "mutating", False)
              or isinstance(fn, ExternFnNode) or calls_itself(id(fn))}
    changed = True
    while changed:                        # add the callers, until a pass adds nothing
        changed = False
        for fn in fns:
            if id(fn) not in effect and facts[id(fn)].callees & effect:
                effect.add(id(fn))
                changed = True
    return effect


def has_effect(expr, effect):
    """Does evaluating `expr` read the input or call a function in `effect`?"""
    if isinstance(expr, ReadNode):
        return True
    if isinstance(expr, (CallNode, MemberCallNode)):
        if expr.callable:
            return has_effect(expr.callable, effect)
        if id(expr.fn) in effect:
            return True
        if isinstance(expr, CallableCallNode) and has_effect(expr.target, effect):
            return True
    return any(has_effect(child, effect) for child in expr.children())


class UseCounter:
    """One walk over the whole tree: for every DeclNode, the number of reads that resolve
    to it, and for every global FnNode, the number of calls of it. Keys are id(node), so
    two declarations named x are two keys."""

    def __init__(self, effect):
        self.effect = effect              # {id(fn)} whose calls may be observed
        self.reads = {}                   # id(DeclNode) -> count
        self.calls = {}                   # id(FnNode) -> count, global functions only

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
        self.calls.setdefault(id(node), 0)
        node.body.accept(self)

    def visit_member_fn(self, node):      # never removed: not counted, only walked
        node.body.accept(self)

    def visit_extern_fn(self, node):      # counted like a global function, no body to walk
        self.calls.setdefault(id(node), 0)

    # -- statements ------------------------------------------------------------

    def visit_decl(self, node):
        self.reads.setdefault(id(node), 0)
        if any(has_effect(value, self.effect) for value in node.values):
            self.read(node)               # kept: its value may print, read or hang
        for value in node.values:
            value.accept(self)

    def visit_lambda(self, node):
        # Not a declaration the pass may remove: only what it reads counts. Its captures
        # are the values of its variable, and its body is the call of its struct.
        for value in node.decl.values:
            value.accept(self)
        node.body.accept(self)

    def visit_assign(self, node):
        if has_effect(node.value, self.effect):   # the target is not a read, unless the value may print
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
        self.calls[id(node.fn)] = self.calls.get(id(node.fn), 0) + 1    # a MemberFnNode never reaches zero
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
    """Rebuilds the program's function list without the dead functions, and every
    statement list without the dead declarations and the assignments to them. The lists
    are rebuilt, never deleted from while they are walked.

    A lambda inside a removed function goes with it, but its struct is also on the
    program's list of lambda structs, which the generator builds from. So the pruner notes
    every lambda still in the tree and drops the struct of each one that is not: its call
    could hold the only call of a function this round removes."""

    def __init__(self, dead, dead_fns):
        self.dead = dead                  # {id(DeclNode)} with no reads
        self.dead_fns = dead_fns          # {id(FnNode)} with no calls
        self.live_lambdas = set()         # {id(StructNode)} of the lambdas still in the tree

    def prune(self, program):
        program.fns = [fn for fn in program.fns if id(fn) not in self.dead_fns]
        for struct in program.structs:
            for method in struct.methods:
                self.prune_block(method.body)
        for fn in program.fns:
            if fn.body is not None:       # an extern has nothing to prune
                self.prune_block(fn.body)
        program.statements = self.prune_statements(program.statements)
        program.lambda_structs = [struct for struct in program.lambda_structs if id(struct) in self.live_lambdas]

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
                self.live_lambdas.add(id(stmt.struct))
                self.prune_block(stmt.body)
            kept.append(stmt)
        return kept


def remove_unused(program):
    """Remove every declaration nobody reads and every global function nobody calls,
    round after round, until a round removes nothing; return the same tree."""
    while True:
        counter = UseCounter(effectful_functions(program))
        program.accept(counter)
        dead = {decl for decl, count in counter.reads.items() if count == 0}
        dead_fns = {fn for fn, count in counter.calls.items() if count == 0}
        if not dead and not dead_fns:
            return program
        Pruner(dead, dead_fns).prune(program)
