"""Phase 2: a walk over the AST that checks it  (semantic errors)

A pass of its own, run on the whole tree before the code generator is even constructed:
it rejects the program before a single instruction exists, and it can be tested without
LLVM at all. It owns the symbol table, and it answers the one question the parser
cannot: what is the type of this expression?

The symbol table is a stack of frames, one per scope, each a dict name -> DeclNode (which
knows the type and the mut flag). The program has the bottom frame; a block pushes an
empty frame on entry and pops it on exit. A declaration goes into the top frame, and only
the top frame is checked for a duplicate -- so a block may declare a name again with any
type: shadowing. A use walks the frames from the top down and takes the first hit; a name
in no frame, including one whose frame has already been popped, is used before its
declaration.

Next to it sits the struct table, name -> StructNode, filled in program order: a field
may only have a built-in type or a struct declared above it, so a struct can never
contain itself, directly or through another one.

Its contract with the code generator is a handful of fields on the tree:
  * `node.type` on every expression node -- "i32", "i64", "bool" or a struct name;
  * `node.decl` on every VarNode, FieldNode and AssignNode -- the declaration the name
    resolved to;
  * `node.indices` on every FieldNode and AssignNode -- the field chain as field numbers;
  * `node.target_type` on every AssignNode, `node.copy` on every DeclNode.
The generator reads those and looks nothing up -- not a variable by its name, not a
field by its name. With shadowing that matters: three x in three scopes are three
declarations, and the pass is the one that knows which is meant.

The type rules:
  * a decimal constant has the narrowest type it fits: i32, else i64, else an error;
  * + - * take two integers and give the wider of the two; a bool operand is an error;
  * == != take two integers (any mix) or two bools, and give a bool;
  * an i32 value may initialise or be assigned to an i64 variable -- and that is the only
    conversion in the language: never the other way round, never across bool;
  * ! takes a bool and gives a bool;
  * the condition of an if or a while is a bool;
  * exit takes an integer or a bool.

The struct rules:
  * an object takes one value per field, in field order, each one assignable to the
    field's type -- or one whole object of its own struct, which is copied;
  * a field chain names a field that exists at every link;
  * writing through x.a.b needs x, a and b all mut;
  * := replaces a whole object (or a struct-typed field) only when every field inside it,
    at every depth, is mut: a const field gets its value only when the object is created.
"""

from .ast_nodes import ConstNode, chain
from .errors import CompileError, error_at

I32_MAX = 2**31 - 1
I64_MAX = 2**63 - 1
INTEGERS = ("i32", "i64")
BUILT_IN = ("i32", "i64", "bool")


def wider(left, right):
    return "i64" if "i64" in (left, right) else "i32"


class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]                # frames, innermost last: name -> DeclNode
        self.structs = {}                 # name -> StructNode, in declaration order

    def run(self, program):
        program.accept(self)
        return program

    def lookup(self, node, name):
        """The declaration `name` means here: the innermost frame that has it wins."""
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        raise error_at(node, f"variable '{name}' is used before its declaration")

    def check_type(self, node):
        """A declaration's type must be built in or a struct declared above it."""
        if node.type_name not in BUILT_IN and node.type_name not in self.structs:
            raise CompileError(*node.type_at, f"unknown type '{node.type_name}'")

    def field_of(self, type_name, link, path):
        """The field `link` names inside a value of `type_name`, reached through `path`."""
        struct = self.structs.get(type_name)
        if struct is None:
            raise error_at(link, f"'{path}' is {type_name}, not a struct: it has no field '{link.name}'")
        for field in struct.fields:
            if field.name == link.name:
                return field
        raise error_at(link, f"struct '{type_name}' has no field '{link.name}'")

    def walk_chain(self, node, check_mut=False):
        """Follow node.name.link.link...; store decl and indices on the node and return the
        type at the end. With check_mut, every link must be mut -- a write."""
        decl = self.lookup(node, node.name)
        target, path, type_name = chain(node.name, node.links), node.name, decl.type_name
        if check_mut and not decl.mutable:
            if not node.links:
                raise error_at(node, f"cannot assign to '{target}': it is not mut")
            raise error_at(node, f"cannot assign to '{target}': '{path}' is not mut")
        indices = []
        for link in node.links:
            field = self.field_of(type_name, link, path)
            path += "." + link.name
            if check_mut and not field.mutable:
                raise error_at(link, f"cannot assign to '{target}': '{path}' is not mut")
            indices.append(field.index)
            type_name = field.type_name
        node.decl, node.indices = decl, indices
        return type_name

    def const_field(self, type_name):
        """The first field inside a `type_name` value, at any depth, that is not mut -- as
        'a.b' -- or None when every one is (and always None for a built-in type)."""
        for field in getattr(self.structs.get(type_name), "fields", []):
            if not field.mutable:
                return field.name
            inner = self.const_field(field.type_name)
            if inner:
                return f"{field.name}.{inner}"
        return None

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
        for struct in node.structs:
            struct.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def visit_struct(self, node):
        if node.name in self.structs:
            prev = self.structs[node.name]
            raise error_at(node, f"struct '{node.name}' is already declared at line {prev.line}:{prev.col}")
        seen = set()
        for index, field in enumerate(node.fields):
            if field.name in seen:
                raise error_at(field, f"struct '{node.name}' already has a field '{field.name}'")
            if field.type_name == node.name:
                raise CompileError(*field.type_at, f"struct '{node.name}' cannot contain itself")
            self.check_type(field)
            seen.add(field.name)
            field.index = index
        self.structs[node.name] = node    # only now: its own fields could not name it

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:  # only this frame: an outer x may be shadowed
            raise error_at(node, f"variable '{node.name}' is already declared in this block")
        self.check_type(node)
        # Typed before the name exists: `i32 t{t}` is an error, and in a block that shadows
        # an outer t, `i64 t{t + 1}` reads the outer one.
        for value in node.values:
            value.accept(self)
        brace, count = node.brace, len(node.values)
        struct = self.structs.get(node.type_name)
        if struct is None:
            if count != 1:
                raise CompileError(*brace, f"'{node.name}' of type {node.type_name} takes one value, got {count}")
            self.check_assignable(node.values[0], node.type_name, node, f"initialise '{node.name}'")
        elif count == 1 and node.values[0].type == node.type_name:
            node.copy = True              # Point q{p}: the whole object
        else:
            if count != len(struct.fields):
                raise CompileError(*brace, f"struct '{struct.name}' has {plural(len(struct.fields), 'field')}, "
                                           f"got {plural(count, 'value')}")
            for field, value in zip(struct.fields, node.values):
                self.check_assignable(value, field.type_name, value, f"initialise '{node.name}.{field.name}'")
        self.scopes[-1][node.name] = node

    def visit_assign(self, node):
        target = chain(node.name, node.links)
        node.target_type = self.walk_chain(node, check_mut=True)
        inner = self.const_field(node.target_type)
        if inner:
            raise error_at(node, f"cannot assign to '{target}': its field '{inner}' is not mut")
        node.value.accept(self)
        self.check_assignable(node.value, node.target_type, node, f"assign to '{target}'")

    def check_condition(self, node, keyword):
        condition = node.condition.accept(self)
        if condition != "bool":
            raise error_at(node, f"the condition of '{keyword}' must be bool, got {condition}")

    def visit_if(self, node):
        self.check_condition(node, "if")
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        # The condition is outside the body's frame: it is evaluated before the body
        # starts and after it ends, so it can only see what the while itself can see.
        self.check_condition(node, "while")
        node.body.accept(self)

    def visit_block(self, node):
        self.scopes.append({})
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit:
            node.exit.accept(self)
        self.scopes.pop()                 # its names are gone: a use after the block is an error

    def visit_exit(self, node):
        value = node.value.accept(self)
        if value not in BUILT_IN:         # printf has no format for a whole object
            raise error_at(node, f"exit takes an integer or a bool, got {value}")

    # -- expressions: return the type, and store it on the node -------------

    def visit_binop(self, node):
        left, right = node.left.accept(self), node.right.accept(self)
        if node.op in ("+", "-", "*"):
            for operand in (left, right):
                if operand not in INTEGERS:
                    raise error_at(node, f"cannot apply '{node.op}' to {operand}")
            node.type = wider(left, right)
        else:                             # == or !=: two integers, or two bools -- never objects
            if not ((left in INTEGERS and right in INTEGERS) or left == right == "bool"):
                raise error_at(node, f"cannot compare {left} with {right}")
            node.type = "bool"
        return node.type

    def visit_not(self, node):
        operand = node.operand.accept(self)
        if operand != "bool":
            raise error_at(node, f"cannot apply '!' to {operand}")
        node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node, node.name)
        node.type = node.decl.type_name
        return node.type

    def visit_field(self, node):
        node.type = self.walk_chain(node)
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


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def check(program):
    """Check a tree and annotate it with types; raise CompileError on the first problem."""
    return SemanticChecker().run(program)
