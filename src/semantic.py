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

A member function is checked like a function, with its struct around it. A name in none
of its frames may still be a field of the object it is called on: x means this.x, so the
name resolves to the function's ThisParam with the field's index first in the chain. A
call f(...) looks among the member functions of the same struct first, then among the
global ones. Every member signature is collected with the global ones, before any body.

The mutation rule needs every body at once. A member function is mutating when it writes
a field of its object, or calls a mutating member function on its object or on one of
its fields -- and member functions may call each other in a cycle. So the member bodies
are checked first, each noting what it writes and what it calls on itself; then the
mutating ones are marked: first those that write a field, then, again and again, every
one that calls a marked one, until a pass marks nothing new. Only then is a member call
inside a member body checked against the rule; the global functions and the top-level
statements come after, so their member calls are checked on the spot. A mutating
function is called only through a chain whose every link is mut: the variable and each
field on the way.

An object whose struct declares a member function named call can be called like a
function: triple(14) is triple.call(14). The parser cannot tell a variable from a function,
so it writes a CallNode either way; the decision is made here, by the names in scope. A
name that is a visible variable -- a local, a parameter, a field inside a member function
-- is an object to call; otherwise it is a function, looked up as before; a name that is
both is refused at the call. For an object the pass puts a CallableCallNode -- the target,
the member function call, the same arguments -- in place of the call, and checks it as the
member call it is: the arguments, the mutation rule, and nothing else to generate. After a
dot the same holds: in s.scale(7), scale is a field of s, so the call is s.scale.call(7).

A lambda is rewritten here too, because only here are the types of its captures known:
fn add_k := [k](i32 y) -> i32 becomes a struct lambda.add_k with one field per capture
(const, or mut for [mut k]), its member function call with the lambda's parameters, result and body, and a
variable add_k of that struct, initialised where the lambda stands with the captured
values -- a copy, so changing k later does not change add_k. The struct joins the
program's lambda_structs; add_k(5) is then the call of an object. The body is checked as
that member function: a fresh stack with its parameters, and the captures as the fields of
its object -- so it sees its parameters, its locals, its captures and the global functions,
nothing else. What stood around it is kept on a stack of its own, only to say that a name
the body uses is visible outside but not captured. The variable is declared after the
body, so a lambda cannot call itself. A struct without a name in the language has a type
no parameter, result, variable or field can have, so a lambda value goes nowhere else.

[mut n] gives the lambda a copy of its own to change: the field is mut, so a call that
assigns it -- or calls a mutating call on it, a captured lambda among them -- is a
mutating member function, and the mutation rule decides where it may be called, as for
any other. The variable of a lambda with a mut capture is mut, so next() works where it
stands; inside another lambda, [next] is a const copy and [mut next] a mutable one. The
variable that was captured never changes.

Its contract with the code generator is a handful of fields on the tree:
  * `node.type` on every expression node -- "i32", "i64", "bool" or a struct name;
  * `node.decl` on every VarNode, FieldNode and AssignNode -- the declaration the name
    resolved to, or the ThisParam of a member function for a field of its object;
  * `node.indices` on every VarNode, FieldNode and AssignNode -- the field chain as field
    numbers;
  * `node.target_type` on every AssignNode, `node.copy` on every DeclNode;
  * `node.fn` on every CallNode and MemberCallNode -- the function it calls -- or
    `node.callable` when it calls an object: the CallableCallNode to emit instead;
  * `node.struct` and `node.this` on every MemberFnNode.
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
  * a parameter is never mut;
  * read() gives an i32; print takes an integer, a bool, a struct object or a string
    literal, and has no value of its own.

The struct rules:
  * an object takes one value per field, in field order, each one assignable to the
    field's type -- or one whole object of its own struct, which is copied;
  * a field chain names a field that exists at every link;
  * writing through x.a.b needs x, a and b all mut;
  * := replaces a whole object (or a struct-typed field) only when every field inside it,
    at every depth, is mut: a const field gets its value only when the object is created;
  * a struct field may be given a {} of its own inside the outer one, at any depth, with
    the same rules; a {} where a built-in value belongs is an error at that '{'.

The member function rules:
  * a field and a member function of one struct do not share a name, nor do two member
    functions;
  * a member function is called on an object of a struct that declares it -- never on a
    type, never on an integer or a bool;
  * a mutating one is called only through a chain of mut links;
  * a call result is a temporary copy: a field of it may be read when it is a struct, and
    any member function may be called on it, a mutating one too.

The callable object rules:
  * name(args) on a variable needs a struct that declares call -- not a struct without
    one, not an integer or a bool -- and is name.call(args);
  * obj.name(args) where name is a field of obj is obj.name.call(args);
  * a name that is both a visible variable and a function cannot be called;
  * a wrong argument, or a mutating call through a const link, is reported for the object,
    not for its call.

The lambda rules:
  * a capture names a variable visible where the lambda stands, once; a parameter does
    not share a name with a capture;
  * inside the body a capture is const unless written [mut n], and a name of the scopes
    around it that is not captured is an error;
  * a lambda with a mut capture is a mut variable; its call is mutating when it changes a
    capture, and is then called only through mut links;
  * a lambda does not call itself;
  * a lambda is not an argument, the value of exit, a value of a declaration, a side of
    an assignment or the value of a print, and its captures are not visible outside it.
"""

from .ast_nodes import (CallableCallNode, CallNode, ConstNode, DeclNode, FieldNode, InitNode, LambdaNode, Link,
                        MemberCallNode, MemberFnNode, MemberNode, ParamNode, ReadNode, ResultFieldNode, StructNode,
                        ThisParam, VarNode, chain)
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
        self.mutation_known = False       # every member function is marked mutating or not
        self.deferred = []                # member calls in member bodies, checked once it is
        self.program = None               # the ProgramNode: the struct of every lambda joins it
        self.lambda_types = {}            # struct name of a lambda -> the lambda's name
        self.lambda_methods = []          # the call of each lambda met before the marking
        self.enclosing = []               # (scopes, current_fn) around each lambda body being checked

    def run(self, program):
        program.accept(self)
        return program

    def find(self, name):
        """The declaration `name` means here, or None: the innermost frame that has it wins.
        In a member function a name in no frame may still be a field of the object -- its
        MemberNode -- so a parameter or a variable hides a field."""
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        if isinstance(self.current_fn, MemberFnNode):
            return named(self.current_fn.struct.fields, name)
        return None

    def lookup(self, node, name):
        decl = self.find(name)
        if decl is not None:
            return decl
        fn = self.current_fn
        lambda_name = getattr(fn, "lambda_name", None)
        if lambda_name and self.visible_outside(name):
            raise error_at(node, f"'{name}' is not captured by lambda '{lambda_name}': "
                                 f"add it to the capture list, [{name}]")
        if fn is not None and name in self.top_level_names:
            raise error_at(node, f"{title(fn)} cannot see the top-level variable "
                                 f"'{name}': a function sees only its parameters and its own variables")
        if lambda_name:
            raise error_at(node, f"'{name}' is not a parameter, a variable or a capture of lambda '{lambda_name}'")
        if isinstance(fn, MemberFnNode):
            raise error_at(node, f"'{name}' is not a parameter, a variable or a field of '{fn.struct.name}'")
        raise error_at(node, f"variable '{name}' is used before its declaration")

    def visible_outside(self, name):
        """Is `name` a variable where a lambda being checked stands -- in any of the scopes
        around it, the fields of a member function among them? Only to say why the body
        does not see it."""
        for scopes, fn in self.enclosing:
            if any(name in frame for frame in scopes):
                return True
            if isinstance(fn, MemberFnNode) and named(fn.struct.fields, name):
                return True
        return False

    def open_lambdas(self):
        """The names of the lambdas whose bodies are being checked: none is declared yet."""
        fns = [self.current_fn, *(fn for _, fn in self.enclosing)]
        return {fn.lambda_name for fn in fns if getattr(fn, "lambda_name", None)}

    def not_lambda(self, expr, what, holder):
        """A lambda's struct has no name in the language, so its value goes nowhere a type
        would have to be written: no parameter, result, variable or field has its type."""
        if expr.type in self.lambda_types:
            raise error_at(expr, f"'{written(expr)}' is a lambda and {what}: "
                                 f"its type has no name, so no {holder} can have it")

    def check_type(self, node):
        """A declaration's type must be built in or a struct declared above it."""
        if node.type_name not in BUILT_IN and node.type_name not in self.structs:
            raise CompileError(*node.type_at, f"unknown type '{node.type_name}'")

    def field_of(self, type_name, link, path):
        """The field `link` names inside a value of `type_name`, reached through `path`."""
        struct = self.structs.get(type_name)
        if struct is None:
            raise error_at(link, f"'{path}' is {type_name}, not a struct: it has no field '{link.name}'")
        if path is not None and type_name in self.lambda_types:   # path is None for its own body
            raise error_at(link, f"'{path}' is a lambda: its captures are not visible outside its body")
        for field in struct.fields:
            if field.name == link.name:
                return field
        raise error_at(link, f"struct '{type_name}' has no field '{link.name}'")

    def walk_chain(self, node, links, check_mut=False):
        """Follow node.name, then `links`; store decl and indices on the node and return the
        type at the end. With check_mut, every link must be mut -- a write. Without it, the
        first link that is not mut is kept in node.const_link, for a mutating call."""
        decl = self.lookup(node, node.name)
        target, path = chain(node.name, links), node.name
        if isinstance(decl, MemberNode):  # a field of the object: x is this.x
            links = [Link(node.name, node.line, node.col), *links]
            decl, path = self.current_fn.this, None
        type_name = decl.type_name
        if check_mut and isinstance(decl, ParamNode):
            raise error_at(node, f"cannot assign to '{target}': '{node.name}' is a parameter, and parameters cannot be changed")
        if check_mut and not decl.mutable:
            if not links:
                raise error_at(node, f"cannot assign to '{target}': it is not mut")
            raise error_at(node, f"cannot assign to '{target}': '{path}' is not mut")
        const_link = None if decl.mutable else path
        indices = []
        for link in links:
            field = self.field_of(type_name, link, path)
            path = link.name if path is None else f"{path}.{link.name}"
            if not field.mutable:
                if (check_mut and isinstance(decl, ThisParam) and link is links[0]
                        and getattr(self.current_fn, "lambda_name", None)):        # x := ... on a capture
                    raise error_at(link, f"cannot assign to '{target}': the capture '{link.name}' is const, "
                                         f"write [mut {link.name}]")
                if check_mut:
                    raise error_at(link, f"cannot assign to '{target}': '{path}' is not mut")
                const_link = const_link or path
            indices.append(field.index)
            type_name = field.type_name
        node.decl, node.indices, node.const_link = decl, indices, const_link
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
        self.program = node
        for struct in node.structs:
            struct.accept(self)
        # The bodies are checked before the statements, so the top-level names are collected
        # first -- only to say why such a name is not visible inside a function.
        self.top_level_names = {s.name for s in node.statements if isinstance(s, (DeclNode, LambdaNode))}
        methods = [method for struct in node.structs for method in struct.methods]
        for struct in node.structs:       # first every signature...
            for method in struct.methods:
                self.check_signature(method)
                method.struct, method.this = struct, ThisParam(struct.name)
        for fn in node.fns:
            self.declare_fn(fn)
        for method in methods:            # ...then every body, which may call any of them:
            method.accept(self)           # the member bodies first, for the mutation rule
        self.mark_mutating([*methods, *self.lambda_methods])     # with the lambdas inside them
        self.mutation_known = True
        for call in self.deferred:
            self.check_mutation(call)
        for fn in node.fns:
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit.accept(self)

    def declare_fn(self, node):
        if node.name in self.fns:
            prev = self.fns[node.name]
            raise error_at(node, f"function '{node.name}' is already declared at line {prev.line}:{prev.col}")
        self.check_signature(node)
        self.fns[node.name] = node

    def check_signature(self, node):
        names = set()
        for param in node.params:
            if param.name in names:
                who = title(node) if getattr(node, "lambda_name", None) else f"function '{node.name}'"
                raise error_at(param, f"{who} already has a parameter '{param.name}'")
            self.check_type(param)
            names.add(param.name)
        if node.result_type not in BUILT_IN and node.result_type not in self.structs:
            raise CompileError(*node.result_at, f"unknown type '{node.result_type}'")

    def mark_mutating(self, methods):
        """Mark the member functions that change their object: those that write a field,
        then every one that calls a marked one on its object or a field, until no pass
        marks a new one -- calls may go round in a cycle, so one pass is not enough."""
        for method in methods:
            method.mutating = method.assigns_field
        changed = True
        while changed:
            changed = False
            for method in methods:
                if not method.mutating and any(callee.mutating for callee in method.calls_on_self):
                    method.mutating = changed = True

    def visit_fn(self, node):
        outer, outer_fn = self.scopes, self.current_fn     # a lambda's body is checked inside another
        # A fresh stack: the parameters, and the body's lines in the same frame. Nothing of
        # the program's frames is on it, so a top-level variable is not visible here.
        self.scopes = [{param.name: param for param in node.params}]
        self.current_fn = node
        for stmt in node.body.statements:
            stmt.accept(self)
        node.body.exit.accept(self)
        self.scopes, self.current_fn = outer, outer_fn

    # A member body is checked the same way: the fields of its object come in through find().
    visit_member_fn = visit_fn

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
        methods = set()
        for method in node.methods:
            if method.name in seen:
                raise error_at(method, f"struct '{node.name}' already has a field '{method.name}': "
                                       f"a member function cannot have the same name")
            if method.name in methods:
                raise error_at(method, f"struct '{node.name}' already has a member function '{method.name}'")
            methods.add(method.name)
        self.structs[node.name] = node    # only now: its own fields could not name it

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:  # only this frame: an outer x may be shadowed
            raise error_at(node, f"variable '{node.name}' is already declared in this block")
        self.check_type(node)
        # Typed before the name exists: `i32 t{t}` is an error, and in a block that shadows
        # an outer t, `i64 t{t + 1}` reads the outer one.
        node.copy = self.check_values(node.values, node.type_name, node.brace, node.name, node)
        self.scopes[-1][node.name] = node

    def visit_lambda(self, node):
        """fn add_k := [k](i32 y) -> i32 becomes what the compiler already has: a struct
        lambda.add_k with one const field per capture, of the captured variable's type; its
        member function call with the lambda's parameters, result and body; and a variable
        add_k of that struct, initialised here with the captured values. From then on
        add_k(5) is the call of an object, like any other."""
        if node.name in self.scopes[-1]:
            raise error_at(node, f"variable '{node.name}' is already declared in this block")
        fields, values = [], []
        for capture in node.captures:
            if named(fields, capture.name):
                raise error_at(capture, f"'{capture.name}' is captured twice")
            if self.find(capture.name) is None:
                raise error_at(capture, f"cannot capture '{capture.name}': no variable '{capture.name}' is visible here")
            value = VarNode(capture.line, capture.col, capture.name)            # the copy is taken here
            # [mut n]: a copy the body may change -- of a const variable too, it is a copy.
            field = MemberNode(capture.line, capture.col, capture.name, value.accept(self), capture.mutable,
                               (capture.line, capture.col))
            field.index = len(fields)
            fields.append(field)
            values.append(value)
        for param in node.params:
            if named(fields, param.name):
                raise error_at(param, f"parameter '{param.name}' of lambda '{node.name}' has the name of a capture")

        # A dot never appears in a name the program declares, so lambda.add_k cannot clash
        # with a struct of its own; two lambdas of one name, in two scopes, get a number.
        struct_name, count = f"lambda.{node.name}", 1
        while struct_name in self.structs:
            count += 1
            struct_name = f"lambda.{node.name}.{count}"
        call = MemberFnNode(node.line, node.col, "call", node.params, node.result_type, node.result_at, node.body)
        struct = StructNode(node.line, node.col, struct_name, fields, [call])
        call.struct, call.this, call.lambda_name = struct, ThisParam(struct_name), node.name
        self.check_signature(call)
        self.structs[struct_name] = struct
        self.lambda_types[struct_name] = node.name
        self.program.lambda_structs.append(struct)

        # The body sees its parameters, its locals and its captures -- the fields of its
        # object, through find() -- and the global functions; nothing from around it.
        self.enclosing.append((self.scopes, self.current_fn))
        call.accept(self)
        self.enclosing.pop()
        if self.mutation_known:           # no cycle: a lambda calls only what is declared above it
            call.mutating = call.assigns_field or any(callee.mutating for callee in call.calls_on_self)
        else:                             # inside a member body: marked with the member functions
            self.lambda_methods.append(call)

        # Declared only now: the body could not call the lambda it belongs to. It is mut when
        # a capture is, so a call that changes that copy works where the lambda stands.
        mutable = any(capture.mutable for capture in node.captures)
        node.decl = DeclNode(node.line, node.col, node.name, struct_name, mutable, values,
                             (node.line, node.col), (node.line, node.col))
        self.scopes[-1][node.name] = node.decl
        node.struct = struct

    def check_values(self, values, type_name, brace, path, at):
        """The values of one {} -- a declaration's, or an inner one -- against the type it
        initialises; `path` names what is being initialised (p, c.center). The same rules at
        every depth: one value per field in field order, or one whole object of the struct,
        which is copied -- the return value says which. A value may be a {} again, but only
        where the field it lands in is a struct."""
        for value in values:
            if not isinstance(value, InitNode):   # an inner {} has no type of its own
                value.accept(self)
                self.not_lambda(value, f"cannot initialise '{path}'", "variable or field")
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
        decl = self.find(node.name)
        if decl is not None and not node.links and decl.type_name in self.lambda_types:
            raise error_at(node, f"cannot assign to '{target}': it is a lambda, and a lambda is never assigned")
        node.target_type = self.walk_chain(node, node.links, check_mut=True)
        if isinstance(node.decl, ThisParam):   # a field of the object: the function is mutating
            self.current_fn.assigns_field = True
        inner = self.const_field(node.target_type)
        if inner:
            raise error_at(node, f"cannot assign to '{target}': its field '{inner}' is not mut")
        node.value.accept(self)
        self.not_lambda(node.value, "cannot be assigned", "variable or field")
        self.check_assignable(node.value, node.target_type, node, f"assign to '{target}'")

    def visit_print(self, node):
        node.value.accept(self)           # anything with a value prints; the literal too -- not a lambda
        if node.value.type in self.lambda_types:
            raise error_at(node.value, f"'{written(node.value)}' is a lambda and cannot be printed: "
                                       f"its captures are not visible outside its body")

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
        self.not_lambda(node.value, "cannot be the value of exit", "result type")
        fn = self.current_fn
        if fn is not None:                # in a function: the result, of the result type
            self.check_assignable(node.value, fn.result_type, node, None,
                                  f"{title(fn)} returns {fn.result_type}, but exit has {value}")
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
        node.type = self.walk_chain(node, [])
        return node.type

    def visit_field(self, node):
        node.type = self.walk_chain(node, node.links)
        return node.type

    def visit_call(self, node):
        """name(...): a visible variable makes it a call of that object, name.call(...);
        otherwise a member function of the same struct, then a global function. A name that
        is both a variable and a function is refused here -- declaring both is not."""
        current = self.current_fn
        lambda_name = getattr(current, "lambda_name", None)
        # The struct of a lambda has only its call, and the lambda cannot call itself.
        method = (named(current.struct.methods, node.name)
                  if isinstance(current, MemberFnNode) and not lambda_name else None)
        fn = method or self.fns.get(node.name)
        if self.find(node.name) is not None:
            if fn is not None:
                raise error_at(node, f"'{node.name}' is both a variable and a function: rename one of them")
            target = VarNode(node.line, node.col, node.name)
            return self.call_object(node, target, node.name)
        if fn is None:
            if node.name in self.open_lambdas():
                raise error_at(node, f"lambda '{node.name}' cannot call itself: "
                                     f"its name is declared after its body")
            if lambda_name and self.visible_outside(node.name):
                raise error_at(node, f"'{node.name}' is not captured by lambda '{lambda_name}': "
                                     f"add it to the capture list, [{node.name}]")
            raise error_at(node, f"function '{node.name}' is not declared")
        if method is not None:            # d2() inside Point: d2 on the same object
            current.calls_on_self.append(method)
        self.check_args(node, fn)
        node.fn = fn
        node.type = fn.result_type
        return node.type

    def call_object(self, node, target, shown):
        """Put target.call(args) in place of `node` and check it as the member call it is."""
        node.callable = CallableCallNode(node.line, node.col, target, node.args, shown)
        node.type = self.visit_member_call(node.callable)
        return node.type

    def call_field(self, node, field):
        """obj.name(...) where name is a field of obj: obj.name.call(...). The object has been
        checked already, so the new target is typed here rather than walked again -- a call
        result in it must not be checked twice."""
        target, link = node.target, Link(node.name, node.line, node.col)
        if isinstance(target, (VarNode, FieldNode)):
            target = FieldNode(target.line, target.col, target.name, [*getattr(target, "links", []), link])
        else:                             # a field of a call result: p.moved(1).scale(3)
            value, links, indices = target, [link], [field.index]
            if isinstance(target, ResultFieldNode):               # c.grown(1).shape.scale(3)
                value, links, indices = target.value, [*target.links, link], [*target.indices, field.index]
            target = ResultFieldNode(node.line, node.col, value, links)
            target.indices, target.type = indices, field.type_name
        return self.call_object(node, target, written(target))

    def visit_member_call(self, node):
        target = node.target
        callable_call = isinstance(node, CallableCallNode)
        named_object = isinstance(target, (VarNode, FieldNode))   # else a call result, or a field of one
        if named_object and self.find(target.name) is None and target.name in self.structs:
            raise error_at(target, f"'{node.name}' is called on the type '{target.name}': "
                                   f"a member function is called on an object")
        type_name = target.type or target.accept(self)            # typed already: see call_field
        struct = self.structs.get(type_name)
        if struct is None:
            if callable_call:
                raise error_at(node, f"'{node.shown}' is not callable: it is {type_name}")
            raise error_at(node, f"'{written(target)}' is {type_name}, not a struct: "
                                 f"it has no member function '{node.name}'")
        fn = named(struct.methods, node.name)
        if fn is None:
            if callable_call:
                raise error_at(node, f"'{node.shown}' is not callable: {type_name} has no member function 'call'")
            field = named(struct.fields, node.name)
            if field is not None:         # s.scale(7): a field, so an object to call
                return self.call_field(node, field)
            raise error_at(node, f"struct '{struct.name}' has no member function '{node.name}'")
        self.check_args(node, fn)
        if named_object and isinstance(target.decl, ThisParam):   # on its own object, or a field of it
            self.current_fn.calls_on_self.append(fn)
        node.fn = fn
        node.type = fn.result_type
        if self.mutation_known:
            self.check_mutation(node)
        else:                             # in a member body: not every function is marked yet
            self.deferred.append(node)
        return node.type

    def check_mutation(self, node):
        """A mutating member function is called only through a chain of mut links. A call
        result is a temporary copy, not a link: changing it changes nobody's object."""
        target = node.target
        if node.fn.mutating and isinstance(target, (VarNode, FieldNode)) and target.const_link:
            if isinstance(node, CallableCallNode):
                why = "it is not mut" if target.const_link == node.shown else f"'{target.const_link}' is not mut"
                raise error_at(node, f"cannot call '{node.shown}': its 'call' changes it, and {why}")
            raise error_at(node, f"cannot call '{node.name}' on '{written(target)}': "
                                 f"it changes the object, and '{target.const_link}' is not mut")

    def visit_result_field(self, node):
        """A field of a call result: the result must be a struct -- an error at the '.' when
        it is not -- and then every link must be a field, as in a chain."""
        type_name, path = node.value.accept(self), written(node.value)
        if type_name not in self.structs:
            raise error_at(node, f"'{path}' is {type_name}, not a struct: it has no field '{node.links[0].name}'")
        indices = []
        for link in node.links:
            field = self.field_of(type_name, link, path)
            path += "." + link.name
            indices.append(field.index)
            type_name = field.type_name
        node.indices, node.type = indices, type_name
        return node.type

    def check_args(self, node, fn):
        """One argument per parameter, each assignable to the parameter's type. A call of an
        object names the object, not its 'call'."""
        name = node.shown if isinstance(node, CallableCallNode) else qualified(fn)
        if len(node.args) != len(fn.params):
            what = f"'{name}'" if isinstance(node, CallableCallNode) else title(fn)
            raise error_at(node, f"{what} takes {plural(len(fn.params), 'argument')}, got {len(node.args)}")
        for param, arg in zip(fn.params, node.args):
            arg.accept(self)
            self.not_lambda(arg, f"cannot be passed to '{name}'", "parameter")
            self.check_assignable(arg, param.type_name, arg, None,
                                  f"argument '{param.name}' of '{name}' is {param.type_name}, got {arg.type}")

    def visit_read(self, node):
        node.type = "i32"
        return node.type

    def visit_string(self, node):
        node.type = "string"              # only ever the value of a print: the parser sees to it
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


def named(nodes, name):
    """The node of `nodes` -- fields or member functions -- called `name`, or None."""
    return next((node for node in nodes if node.name == name), None)


def written(expr):
    """An object as the program wrote it, for a message: p, c.center, p.moved(...).x."""
    if isinstance(expr, MemberCallNode):
        return f"{written(expr.target)}.{expr.name}(...)"
    if isinstance(expr, CallNode):
        return f"{expr.name}(...)"
    if isinstance(expr, ReadNode):
        return "read()"
    if isinstance(expr, ResultFieldNode):
        return written(expr.value) + "".join(f".{link.name}" for link in expr.links)
    return chain(expr.name, getattr(expr, "links", []))


def qualified(fn):
    """A function's name in a message: Point.shift for a member function, add_k for the
    call of a lambda."""
    if isinstance(fn, MemberFnNode):
        return fn.lambda_name or f"{fn.struct.name}.{fn.name}"
    return fn.name


def title(fn):
    """A function in a message, with what it is: function 'sq', lambda 'add_k'."""
    return f"lambda '{fn.lambda_name}'" if getattr(fn, "lambda_name", None) else f"function '{qualified(fn)}'"


def check(program):
    """Check a tree and annotate it with types; raise CompileError on the first problem."""
    return SemanticChecker().run(program)
