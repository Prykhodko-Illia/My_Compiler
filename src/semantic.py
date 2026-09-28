"""Phase 2: a walk over the AST that checks it  (semantic errors)

A pass of its own, run on the whole tree before the code generator is even constructed:
it rejects the program before a single instruction exists, and it can be tested without
LLVM at all. It owns the symbol table (name -> DeclNode, which knows the type and the
mut flag), and it answers the one question the parser cannot: what is the type of this
expression?

Its contract with the code generator is two fields on the tree:
  * `node.type` on every expression node -- "i32", "i64" or "bool";
  * `node.decl` on every VarNode and AssignNode -- the declaration the name resolved to.
The generator reads those and looks nothing up.

The type rules:
  * a decimal constant has the narrowest type it fits: i32, else i64, else an error;
  * + - * take two integers and give the wider of the two; a bool operand is an error;
  * == != take two integers (any mix) or two bools, and give a bool;
  * an i32 value may initialise or be assigned to an i64 variable -- and that is the only
    conversion in the language: never the other way round, never across bool;
  * exit takes an integer or a bool.
"""

from .ast_nodes import ConstNode
from .errors import error_at

I32_MAX = 2**31 - 1
I64_MAX = 2**63 - 1
INTEGERS = ("i32", "i64")


def wider(left, right):
    return "i64" if "i64" in (left, right) else "i32"


class SemanticChecker:
    def __init__(self):
        self.symbols = {}                 # name -> DeclNode

    def run(self, program):
        program.accept(self)
        return program

    def lookup(self, node, name):
        if name not in self.symbols:
            raise error_at(node, f"variable '{name}' is used before its declaration")
        return self.symbols[name]

    def check_assignable(self, expr, want, at, what):
        """Is a value of type expr.type allowed where a `want` is expected?"""
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return                        # the one widening the language does for you
        if isinstance(expr, ConstNode) and have == "i64" and want == "i32":
            raise error_at(expr, f"constant {expr.value} does not fit in i32")
        raise error_at(at, f"cannot {what} of type {want} with a value of type {have}")

    # -- statements ---------------------------------------------------------

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_decl(self, node):
        if node.name in self.symbols:
            prev = self.symbols[node.name]
            raise error_at(node, f"variable '{node.name}' is already declared at line {prev.line}:{prev.col}")
        node.init.accept(self)            # typed before the name exists: `i32 t{t}` is an error
        self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.symbols[node.name] = node

    def visit_assign(self, node):
        decl = self.lookup(node, node.name)
        if not decl.mutable:
            raise error_at(node, f"cannot assign to '{node.name}': it is not mut")
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")
        node.decl = decl

    def visit_exit(self, node):
        value_type = node.value.accept(self)
        if value_type not in (*INTEGERS, "bool"):
            raise error_at(node, f"cannot exit with a value of type {value_type}")

    # -- expressions: return the type, and store it on the node -------------

    def visit_binop(self, node):
        left, right = node.left.accept(self), node.right.accept(self)
        if node.op in ("+", "-", "*"):
            for operand in (left, right):
                if operand not in INTEGERS:
                    raise error_at(node, f"cannot apply '{node.op}' to {operand}")
            node.type = wider(left, right)
        else:                             # == or !=
            if (left in INTEGERS) != (right in INTEGERS):
                raise error_at(node, f"cannot compare {left} with {right}")
            node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node, node.name)
        node.type = node.decl.type_name
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type

    def visit_const(self, node):
        if node.value <= I32_MAX:
            node.type = "i32"
        elif node.value <= I64_MAX:
            node.type = "i64"
        else:
            raise error_at(node, f"constant {node.value} does not fit in i64 (max {I64_MAX})")
        return node.type


def check(program):
    """Check a tree and annotate it with types; raise CompileError on the first problem."""
    return SemanticChecker().run(program)
