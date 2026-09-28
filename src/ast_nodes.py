"""The abstract syntax tree.

The grammar dictates the classes: where a rule says "either / or" there is an abstract
base with one subclass per alternative, and where it says "this, then that" there is a
class whose fields are the parts that carry information. Quoted words disappear or
become flags -- "mut" is a boolean, the braces and ":=" leave nothing behind.

    Node
    +-- ProgramNode      statements: [StmtNode], exit: ExitNode
    +-- StmtNode   (abstract)
    |   +-- DeclNode     name, mutable, init: ExprNode
    |   +-- AssignNode   name, value: ExprNode
    +-- ExitNode         value: ExprNode
    +-- ExprNode   (abstract)
        +-- BinOpNode    op, left: ExprNode, right: ExprNode
        +-- VarNode      name
        +-- ConstNode    value

Every node keeps the line and column of the token it came from -- the name for a
declaration or an assignment, the operator for an operation -- so the walk that emits
the IR can say where 'x' is not mut without having any tokens left. The tree holds
names, numbers, flags and child nodes: never a token.

`accept(visitor)` calls the visitor method for that node kind, so each walk over the
tree (the code generator now, a semantic pass later) is its own class.

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
    """An expression: it produces a value."""


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
    def __init__(self, line, col, name, mutable, init):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.mutable = mutable
        self.init = init                  # ExprNode

    def label(self):
        return f"Decl {self.name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)       # position of the name
        self.name = name
        self.value = value                # ExprNode

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


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
        self.op = op                      # '+', '-', '*'
        self.left = left                  # ExprNode
        self.right = right                # ExprNode

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]

    def accept(self, visitor):
        return visitor.visit_binop(self)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def accept(self, visitor):
        return visitor.visit_const(self)
