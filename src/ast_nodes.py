"""The abstract syntax tree.

The grammar dictates the classes: where a rule says "either / or" there is an abstract
base with one subclass per alternative, and where it says "this, then that" there is a
class whose fields are the parts that carry information. Quoted words disappear or
become flags -- "mut" is a boolean, "else" is whether else_block is set, the braces of
an initialiser and ":=" leave nothing behind (the braces of a block become a BlockNode).

    Node
    +-- ProgramNode      statements: [StmtNode], exit: ExitNode
    +-- StmtNode   (abstract)
    |   +-- DeclNode     name, type_name, mutable, init: ExprNode
    |   +-- AssignNode   name, value: ExprNode
    |   +-- IfNode       condition: ExprNode, then_block: BlockNode, else_block: BlockNode | None
    +-- BlockNode        statements: [StmtNode], exit: ExitNode | None
    +-- ExitNode         value: ExprNode
    +-- ExprNode   (abstract)      -- also carries `type`, filled in by the semantic pass
        +-- BinOpNode    op ('+', '-', '*', '==', '!='), left, right: ExprNode
        +-- NotNode      operand: ExprNode
        +-- VarNode      name      -- also carries `decl`, filled in by the semantic pass
        +-- BoolNode     value (True / False)
        +-- ConstNode    value

Every node keeps the line and column of the token it came from -- the name for a
declaration or an assignment, the operator for an operation, the 'if' for an if, the
'{' for a block -- so the walks that come
after the parser can say where 'x' is not mut without having any tokens left. The tree
holds names, numbers, flags and child nodes: never a token.

Two fields are written by the semantic pass and read by the code generator, which is
their whole contract: `type` on every expression node ("i32", "i64" or "bool"), and
`decl` on every VarNode and AssignNode (the DeclNode the name resolved to).

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


class StmtNode(Node):
    """A statement: it does something and produces no value."""


class ExprNode(Node):
    """An expression: it produces a value, whose type the semantic pass fills in."""

    def __init__(self, line, col):
        super().__init__(line, col)
        self.type = None                  # "i32" | "i64" | "bool", set by the semantic pass


class ProgramNode(Node):
    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)
        self.statements = statements      # [StmtNode]
        self.exit = exit                  # ExitNode

    def label(self):
        return "Program"

    def children(self):
        return [*self.statements, self.exit]

    def accept(self, visitor):
        return visitor.visit_program(self)


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, init):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.type_name = type_name        # "i32" | "i64" | "bool", as written in the source
        self.mutable = mutable
        self.init = init                  # ExprNode

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.value = value                # ExprNode
        self.decl = None                  # DeclNode, set by the semantic pass

    def label(self):
        return f"Assign {self.name}"

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


class BlockNode(Node):
    """The lines between '{' and '}'. Not a statement of its own: it only follows an if
    or an else. It may end with an exit, like the program does."""

    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)       # position of the '{'
        self.statements = statements      # [StmtNode], at least one line in all
        self.exit = exit                  # ExitNode, or None

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
