"""The abstract syntax tree.

The grammar dictates the classes: where a rule says "either / or" there is an abstract
base with one subclass per alternative, and where it says "this, then that" there is a
class whose fields are the parts that carry information. Quoted words disappear or
become flags -- "mut" is a boolean, "else" is whether else_block is set, the braces of
an initialiser and ":=" leave nothing behind (the braces of a block become a BlockNode).

    Node
    +-- ProgramNode      structs: [StructNode], fns: [FnNode], statements: [StmtNode], exit: ExitNode
    +-- StructNode       name, fields: [FieldDeclNode]
    +-- FieldDeclNode    name, type_name, mutable  -- also carries `index`, from the semantic pass
    +-- FnNode           name, params: [ParamNode], result_type, body: BlockNode
    +-- ParamNode        name, type_name   -- a declaration: never mut, so never assigned
    +-- StmtNode   (abstract)
    |   +-- DeclNode     name, type_name, mutable, values: [ExprNode]
    |   +-- AssignNode   name, links: [Link], value: ExprNode
    |   +-- IfNode       condition: ExprNode, then_block: BlockNode, else_block: BlockNode | None
    |   +-- WhileNode    condition: ExprNode, body: BlockNode
    +-- BlockNode        statements: [StmtNode], exit: ExitNode | None
    +-- ExitNode         value: ExprNode
    +-- ExprNode   (abstract)      -- also carries `type`, filled in by the semantic pass
        +-- BinOpNode    op ('+', '-', '*', '==', '!='), left, right: ExprNode
        +-- NotNode      operand: ExprNode
        +-- VarNode      name      -- also carries `decl`, filled in by the semantic pass
        +-- FieldNode    name, links: [Link]   -- c.center.x: the variable, then each field
        +-- CallNode     name, args: [ExprNode] -- also carries `fn`, from the semantic pass
        +-- BoolNode     value (True / False)
        +-- ConstNode    value

Every node keeps the line and column of the token it came from -- the name for a
declaration, an assignment or a struct, the operator for an operation, the 'if' for an
if, the 'while' for a while, the '{' for a block -- so the walks that come after the
parser can say where 'x' is not mut without having any tokens left. Each `.name` of a
field chain is a Link with a position of its own, so an unknown field is reported at
the field. The tree holds names, numbers, flags and child nodes: never a token.

A type is a name: "i32", "i64", "bool", or the name of a struct. Two types are equal when
their names are.

The semantic pass writes, and the code generator reads -- which is their whole contract:
  * `type` on every expression node;
  * `decl` on every VarNode, FieldNode and AssignNode (the DeclNode or ParamNode the name
    resolved to);
  * `fn` on every CallNode (the FnNode it calls);
  * `indices` on every FieldNode and AssignNode: the field chain as field numbers, so
    c.center.x is [0, 0] -- the generator never looks a field up by name;
  * `target_type` on every AssignNode: the type of what is written, the variable's or
    the last field's;
  * `copy` on every DeclNode: true when a struct is initialised from one whole object.

`accept(visitor)` calls the visitor method for that node kind, so each walk over the
tree (the semantic pass, then the code generator) is its own class.

(Not named ast.py: that would shadow the standard library's `ast` module.)
"""


class Node:
    def __init__(self, line, col):
        self.line = line
        self.col = col

    def label(self):
        """This node as one line of text, without its children."""
        raise NotImplementedError

    def children(self):
        return []

    def accept(self, visitor):
        raise NotImplementedError

    def dump(self, depth=0):
        """The tree as text, one node per line, children indented by two spaces."""
        out = "  " * depth + self.label() + "\n"
        for child in self.children():
            out += child.dump(depth + 1)
        return out


class Link:
    """One `.name` of a field chain, where it was written."""

    def __init__(self, name, line, col):
        self.name = name
        self.line = line
        self.col = col


def chain(name, links):
    return ".".join([name, *(link.name for link in links)])


class StmtNode(Node):
    """A statement: it does something and produces no value."""


class ExprNode(Node):
    """An expression: it produces a value, whose type the semantic pass fills in."""

    def __init__(self, line, col):
        super().__init__(line, col)
        self.type = None                  # "i32" | "i64" | "bool" | a struct name, set by the semantic pass


class ProgramNode(Node):
    def __init__(self, line, col, structs, fns, statements, exit):
        super().__init__(line, col)
        self.structs = structs            # [StructNode], first
        self.fns = fns                    # [FnNode], after the structs, before the statements
        self.statements = statements      # [StmtNode]
        self.exit = exit                  # ExitNode

    def label(self):
        return "Program"

    def children(self):
        return [*self.structs, *self.fns, *self.statements, self.exit]

    def accept(self, visitor):
        return visitor.visit_program(self)


class StructNode(Node):
    def __init__(self, line, col, name, fields):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.fields = fields              # [FieldDeclNode], at least one, in declaration order

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields

    def accept(self, visitor):
        return visitor.visit_struct(self)


class FieldDeclNode(Node):
    def __init__(self, line, col, name, type_name, mutable, type_at):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.type_name = type_name
        self.type_at = type_at            # (line, col) of the type, where an unknown one is reported
        self.mutable = mutable
        self.index = None                 # its place in the struct, set by the semantic pass

    def label(self):
        return f"FieldDecl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"


class FnNode(Node):
    def __init__(self, line, col, name, params, result_type, result_at, body):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.params = params              # [ParamNode], maybe none
        self.result_type = result_type    # the type after '->'
        self.result_at = result_at        # (line, col) of that type
        self.body = body                  # BlockNode: its last line is an exit

    def label(self):
        return f"Fn {self.name} -> {self.result_type}"

    def children(self):
        return [*self.params, self.body]

    def accept(self, visitor):
        return visitor.visit_fn(self)


class ParamNode(Node):
    """A parameter. It stands where a DeclNode would for a name: VarNode.decl may be one,
    and the generator keys its slot by it the same way."""

    def __init__(self, line, col, name, type_name, type_at):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.type_name = type_name
        self.type_at = type_at            # (line, col) of the type
        self.mutable = False              # parameters are copies and cannot be changed

    def label(self):
        return f"Param {self.name} {self.type_name}"


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, values, brace, type_at):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.type_name = type_name        # "i32" | "i64" | "bool" | a struct name, as written
        self.type_at = type_at            # (line, col) of the type, where an unknown one is reported
        self.mutable = mutable
        self.values = values              # [ExprNode]: the values between the braces
        self.brace = brace                # (line, col) of the '{', where a wrong count is reported
        self.copy = False                 # one whole object of the same struct, set by the semantic pass

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return self.values

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, links, value):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.links = links                # [Link]: empty for x := ..., [center, x] for c.center.x := ...
        self.value = value                # ExprNode
        self.decl = None                  # DeclNode, set by the semantic pass
        self.indices = []                 # the links as field numbers, set by the semantic pass
        self.target_type = None           # the type written to, set by the semantic pass

    def label(self):
        return f"Assign {chain(self.name, self.links)}"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


class IfNode(StmtNode):
    def __init__(self, line, col, condition, then_block, else_block):
        super().__init__(line, col)       # position of the 'if' keyword
        self.condition = condition        # ExprNode
        self.then_block = then_block      # BlockNode
        self.else_block = else_block      # BlockNode, or None when there is no else

    def label(self):
        return "If"

    def children(self):
        return [self.condition, self.then_block] + ([self.else_block] if self.else_block else [])

    def accept(self, visitor):
        return visitor.visit_if(self)


class WhileNode(StmtNode):
    def __init__(self, line, col, condition, body):
        super().__init__(line, col)       # position of the 'while' keyword
        self.condition = condition        # ExprNode
        self.body = body                  # BlockNode

    def label(self):
        return "While"

    def children(self):
        return [self.condition, self.body]

    def accept(self, visitor):
        return visitor.visit_while(self)


class BlockNode(Node):
    """The lines between '{' and '}'. Not a statement of its own: it only follows an if,
    an else, a while or a function's signature. It may end with an exit, like the program
    does; a function's body must."""

    def __init__(self, line, col, statements, exit, close):
        super().__init__(line, col)       # position of the '{'
        self.statements = statements      # [StmtNode], at least one line in all
        self.exit = exit                  # ExitNode, or None
        self.close = close                # (line, col) of the '}'

    def label(self):
        return "Block"

    def children(self):
        return self.statements + ([self.exit] if self.exit else [])

    def accept(self, visitor):
        return visitor.visit_block(self)


class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)       # position of the 'exit' keyword
        self.value = value                # ExprNode

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_exit(self)


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)       # position of the operator
        self.op = op                      # '+', '-', '*', '==', '!='
        self.left = left                  # ExprNode
        self.right = right                # ExprNode

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]

    def accept(self, visitor):
        return visitor.visit_binop(self)


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)       # position of the '!'
        self.operand = operand            # ExprNode

    def label(self):
        return "Not"

    def children(self):
        return [self.operand]

    def accept(self, visitor):
        return visitor.visit_not(self)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name
        self.decl = None                  # DeclNode, set by the semantic pass

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)


class FieldNode(ExprNode):
    """A field read: c.center.x. A bare variable stays a VarNode."""

    def __init__(self, line, col, name, links):
        super().__init__(line, col)       # position of the variable's name
        self.name = name
        self.links = links                # [Link], at least one
        self.decl = None                  # DeclNode of the variable, set by the semantic pass
        self.indices = []                 # the links as field numbers, set by the semantic pass

    def label(self):
        return f"Field {chain(self.name, self.links)}"

    def accept(self, visitor):
        return visitor.visit_field(self)


class CallNode(ExprNode):
    def __init__(self, line, col, name, args):
        super().__init__(line, col)       # position of the function's name
        self.name = name
        self.args = args                  # [ExprNode]
        self.fn = None                    # FnNode, set by the semantic pass

    def label(self):
        return f"Call {self.name}"

    def children(self):
        return self.args

    def accept(self, visitor):
        return visitor.visit_call(self)


class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value                # True | False

    def label(self):
        return f"Bool {'true' if self.value else 'false'}"

    def accept(self, visitor):
        return visitor.visit_bool(self)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def accept(self, visitor):
        return visitor.visit_const(self)
