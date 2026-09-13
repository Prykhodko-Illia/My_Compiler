"""AST nodes

Expressions produce a value:  Const | Var | BinOp
Statements are one per line:  Declare | Assign | Exit
Nodes keep the tokens they came from, so every error can point at line:column.

(Not named ast.py: that would shadow the standard library's `ast` module.)
"""

from dataclasses import dataclass

from .lexer import Token


@dataclass
class Const:
    value: int
    token: Token

@dataclass
class Var:
    name: str
    token: Token

@dataclass
class BinOp:
    op: str          # '+', '-', '*'
    left: object     # Const | Var
    right: object    # Const | Var

@dataclass
class Declare:
    name: Token
    mutable: bool
    init: object     # Const | Var | BinOp
    first: Token     # the statement's first token

@dataclass
class Assign:
    target: Token
    expr: object     # Const | Var | BinOp
    first: Token

@dataclass
class Exit:
    operand: object  # Const | Var
    first: Token
