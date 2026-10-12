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

A function body gets a stack of its own: its bottom frame holds the parameters, and the
body's own lines go into that same frame, so a parameter cannot be declared again. The
program's frames are not on that stack -- a function sees its parameters, its variables
and the functions, never a top-level variable.

Functions sit in a table of their own, name -> FnNode. It is filled with every signature
before any body is checked, so a function may call itself or one declared below it.

Next to it sits the struct table, name -> StructNode, filled in program order: a field
may only have a built-in type or a struct declared above it, so a struct can never
contain itself, directly or through another one.

Its contract with the code generator is a handful of fields on the tree:
  * `node.type` on every expression node -- "i32", "i64", "bool" or a struct name;
  * `node.decl` on every VarNode, FieldNode and AssignNode -- the declaration the name
    resolved to;
  * `node.indices` on every FieldNode and AssignNode -- the field chain as field numbers;
  * `node.target_type` on every AssignNode, `node.copy` on every DeclNode;
  * `node.fn` on every CallNode -- the function it calls.
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
  * exit at the top level takes an integer or a bool; exit in a function takes a value of
    its result type, which may be a struct;
  * a call has its function's result type, and takes one argument per parameter, each
    assignable to the parameter's type -- so an i32 may go into an i64 parameter;
  * a parameter is never mut.

The struct rules:
  * an object takes one value per field, in field order, each one assignable to the
    field's type -- or one whole object of its own struct, which is copied;
  * a field chain names a field that exists at every link;
  * writing through x.a.b needs x, a and b all mut;
  * := replaces a whole object (or a struct-typed field) only when every field inside it,
    at every depth, is mut: a const field gets its value only when the object is created;
  * a struct field may be given a {} of its own inside the outer one, at any depth, with
    the same rules; a {} where a built-in value belongs is an error at that '{'.
"""

from .ast_nodes import ConstNode, DeclNode, InitNode, ParamNode, chain
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
        self.fns = {}                     # name -> FnNode, every signature before any body
        self.current_fn = None            # the FnNode whose body is being checked, or None
        self.top_level_names = set()      # names the program declares outside any block

    def run(self, program):
        program.accept(self)
        return program

    def lookup(self, node, name):
        """The declaration `name` means here: the innermost frame that has it wins."""
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        if self.current_fn is not None and name in self.top_level_names:
            raise error_at(node, f"function '{self.current_fn.name}' cannot see the top-level variable "
                                 f"'{name}': a function sees only its parameters and its own variables")
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
        if check_mut and isinstance(decl, ParamNode):
            raise error_at(node, f"cannot assign to '{target}': '{node.name}' is a parameter, and parameters cannot be changed")
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

    def check_assignable(self, expr, want, at, what, message=None):
        """Is a value of type expr.type allowed where a `want` is expected?"""
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return                        # the one widening the language does for you
        if isinstance(expr, ConstNode) and have == "i64" and want == "i32":
            raise error_at(expr, f"constant {expr.value} does not fit in i32")
        raise error_at(at, message or f"cannot {what} of type {want} with a value of type {have}")

    # -- statements ---------------------------------------------------------

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        # The bodies are checked before the statements, so the top-level names are collected
        # first -- only to say why such a name is not visible inside a function.
        self.top_level_names = {s.name for s in node.statements if isinstance(s, DeclNode)}
        for fn in node.fns:               # first every signature...
            self.declare_fn(fn)
        for fn in node.fns:               # ...then every body, which may call any of them
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def declare_fn(self, node):
        if node.name in self.fns:
            prev = self.fns[node.name]
            raise error_at(node, f"function '{node.name}' is already declared at line {prev.line}:{prev.col}")
        names = set()
        for param in node.params:
            if param.name in names:
                raise error_at(param, f"function '{node.name}' already has a parameter '{param.name}'")
            self.check_type(param)
            names.add(param.name)
        if node.result_type not in BUILT_IN and node.result_type not in self.structs:
            raise CompileError(*node.result_at, f"unknown type '{node.result_type}'")
        self.fns[node.name] = node

    def visit_fn(self, node):
        outer = self.scopes
        # A fresh stack: the parameters, and the body's lines in the same frame. Nothing of
        # the program's frames is on it, so a top-level variable is not visible here.
        self.scopes = [{param.name: param for param in node.params}]
        self.current_fn = node
        for stmt in node.body.statements:
            stmt.accept(self)
        node.body.exit.accept(self)
        self.scopes, self.current_fn = outer, None

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
        node.copy = self.check_values(node.values, node.type_name, node.brace, node.name, node)
        self.scopes[-1][node.name] = node

    def check_values(self, values, type_name, brace, path, at):
        """The values of one {} -- a declaration's, or an inner one -- against the type it
        initialises; `path` names what is being initialised (p, c.center). The same rules at
        every depth: one value per field in field order, or one whole object of the struct,
        which is copied -- the return value says which. A value may be a {} again, but only
        where the field it lands in is a struct."""
        for value in values:
            if not isinstance(value, InitNode):   # an inner {} has no type of its own
                value.accept(self)
        count = len(values)
        struct = self.structs.get(type_name)
        if struct is None:                        # a built-in type: exactly one plain value
            if count != 1:
                raise CompileError(*brace, f"'{path}' of type {type_name} takes one value, got {count}")
            self.check_not_init(values[0], path, type_name)
            self.check_assignable(values[0], type_name, at, f"initialise '{path}'")
            return False
        if count == 1 and not isinstance(values[0], InitNode) and values[0].type == type_name:
            return True                           # Point q{p}: the whole object
        if count != len(struct.fields):
            raise CompileError(*brace, f"struct '{struct.name}' has {plural(len(struct.fields), 'field')}, "
                                       f"got {plural(count, 'value')}")
        for field, value in zip(struct.fields, values):
            field_path = f"{path}.{field.name}"
            if isinstance(value, InitNode):       # Circle c{{10, 20}, 5}: the field's own {}
                self.check_not_init(value, field_path, field.type_name)
                value.type = field.type_name
                value.copy = self.check_values(value.values, field.type_name, (value.line, value.col),
                                               field_path, value)
            else:
                self.check_assignable(value, field.type_name, value, f"initialise '{field_path}'")
        return False

    def check_not_init(self, value, path, type_name):
        """A {} makes a struct; where a built-in value is expected it is an error at its '{'."""
        if isinstance(value, InitNode) and type_name not in self.structs:
            raise error_at(value, f"'{path}' is {type_name}, not a struct: it takes a value, not {{}}")

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
        fn = self.current_fn
        if fn is not None:                # in a function: the result, of the result type
            self.check_assignable(node.value, fn.result_type, node, None,
                                  f"function '{fn.name}' returns {fn.result_type}, but exit has {value}")
        elif value not in BUILT_IN:       # printf has no format for a whole object
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

    def visit_call(self, node):
        fn = self.fns.get(node.name)
        if fn is None:
            raise error_at(node, f"function '{node.name}' is not declared")
        if len(node.args) != len(fn.params):
            raise error_at(node, f"function '{fn.name}' takes {plural(len(fn.params), 'argument')}, "
                                 f"got {len(node.args)}")
        for param, arg in zip(fn.params, node.args):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg, None,
                                  f"argument '{param.name}' of '{fn.name}' is {param.type_name}, got {arg.type}")
        node.fn = fn
        node.type = fn.result_type
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
