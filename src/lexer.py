"""Phase 0: bytes -> tokens  (lexical errors only)

A hand-written state machine: one loop, one byte per step. The state says
what we are in the middle of (nothing, a word, a number, a string, a ':', an '=', a '!',
a '-'), the byte's class says what happens next. A byte that ends a token is read again
in START (`continue` without advancing) -- the only byte ever re-examined.

The two-byte operators ('==', '!=', ':=', '->') each get a state that accepts exactly
one more byte, so the scanner never looks ahead. '!' and '-' are the states with two
ways out: '=' makes '!=' and '>' makes '->', anything else makes a lone '!' or '-' and
is read again.

'{' and '}' are plain tokens: a block spans lines now, so pairing them is the
parser's job, not the lexer's.

A string literal is '"', then any bytes but '"' and a newline, then '"'. There are no
escape sequences: a backslash is a byte like any other, so \\n is two of them. Its token
keeps the bytes exactly, one character per byte (latin-1), and a literal that reaches the
end of its line is an error at its opening '"'.
"""

from dataclasses import dataclass

from .errors import CompileError


@dataclass
class Token:
    kind: str        # keyword | identifier | constant | string | block | operator | separator | endline
    text: str
    line: int
    col: int         # 1-based position of the token's first byte
    sub: str = ""    # typename | specifier | statement | branch | loop | definition | boolean | builtin
                     # numeric | start | end | assign | plus | minus | times | eq | ne | not
                     # dot | arrow | comma | open | close | capture_open | capture_close

    def __str__(self):
        text = "\\n" if self.kind == "endline" else self.text
        if self.kind == "string":
            text = f'"{text}"'
        fields = ", ".join(f for f in (text, self.kind, self.sub) if f)
        return f"({fields}) {self.line}:{self.col}"


KEYWORDS = {
    b"i32": "typename",
    b"i64": "typename",
    b"bool": "typename",
    b"mut": "specifier",
    b"exit": "statement",
    b"if": "branch",
    b"else": "branch",
    b"while": "loop",
    b"struct": "definition",
    b"fn": "definition",
    b"true": "boolean",
    b"false": "boolean",
    b"read": "builtin",              # read() -- a value
    b"print": "builtin",             # print(x) -- a statement
}

SINGLE_BYTE = {                  # tokens that are complete after one byte
    ord("{"): ("block", "start"),
    ord("}"): ("block", "end"),
    ord("+"): ("operator", "plus"),
    ord("*"): ("operator", "times"),
    ord("."): ("operator", "dot"),          # c.center.x
    ord(","): ("separator", "comma"),       # Point p{10, 20}, f(a, b)
    ord("("): ("separator", "open"),        # only around parameters and arguments:
    ord(")"): ("separator", "close"),       # there are no grouping parentheses
    ord("["): ("separator", "capture_open"),   # only around a lambda's captures:
    ord("]"): ("separator", "capture_close"),  # fn add_k := [k](i32 y) -> i32
}

SPACE, TAB, NEWLINE = 32, 9, 10
COLON, EQUALS, BANG, MINUS, GREATER = ord(":"), ord("="), ord("!"), ord("-"), ord(">")
QUOTE = ord('"')


def is_alpha(b):
    return 65 <= b <= 90 or 97 <= b <= 122 or b == 95      # A-Z a-z _


def is_digit(b):
    return 48 <= b <= 57                                   # 0-9


def show_byte(b):
    return repr(chr(b)) if 33 <= b <= 126 else f"0x{b:02x}"


def lex(data: bytes):
    """Return a list of lines, each a list of tokens ending with an endline token
    (the last line may lack it if the file has no trailing newline)."""
    lines, tokens = [], []
    state, start, start_col = "START", 0, 1
    line, col = 1, 1
    i = 0
    while i <= len(data):                    # one extra step: the end of input
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None or b == NEWLINE:
                if b is None:
                    break
                tokens.append(Token("endline", "\n", line, col))
                lines.append(tokens)
                tokens = []
                line, col = line + 1, 0      # col becomes 1 at the bottom of the loop
            elif b in (SPACE, TAB):
                pass
            elif is_alpha(b):
                state, start, start_col = "IDENT", i, col
            elif is_digit(b):
                state, start, start_col = "NUMBER", i, col
            elif b == COLON:
                state, start_col = "COLON", col
            elif b == EQUALS:
                state, start_col = "EQ", col
            elif b == BANG:
                state, start_col = "BANG", col
            elif b == MINUS:
                state, start_col = "MINUS", col
            elif b == QUOTE:
                state, start, start_col = "STRING", i + 1, col
            elif b in SINGLE_BYTE:
                kind, sub = SINGLE_BYTE[b]
                tokens.append(Token(kind, chr(b), line, col, sub))
            else:
                raise CompileError(line, col, f"unexpected byte {show_byte(b)}")

        elif state == "IDENT":
            if b is not None and (is_alpha(b) or is_digit(b)):
                pass
            else:                            # the word is complete: keyword or identifier?
                word = data[start:i]
                if word in KEYWORDS:
                    tokens.append(Token("keyword", word.decode(), line, start_col, KEYWORDS[word]))
                else:
                    tokens.append(Token("identifier", word.decode(), line, start_col))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                pass
            elif b is not None and is_alpha(b):
                raise CompileError(line, start_col, f"invalid number {data[start:i + 1].decode()!r}: a letter inside a number")
            else:
                tokens.append(Token("constant", data[start:i].decode(), line, start_col, "numeric"))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "COLON":
            if b == EQUALS:
                tokens.append(Token("operator", ":=", line, start_col, "assign"))
                state = "START"
            else:
                raise CompileError(line, start_col, "':' must be followed by '='")

        elif state == "EQ":
            if b == EQUALS:
                tokens.append(Token("operator", "==", line, start_col, "eq"))
                state = "START"
            else:
                raise CompileError(line, start_col, "expected '==' (a single '=' is not an operator)")

        elif state == "BANG":
            if b == EQUALS:
                tokens.append(Token("operator", "!=", line, start_col, "ne"))
                state = "START"
            else:                            # a lone '!': the negation
                tokens.append(Token("operator", "!", line, start_col, "not"))
                state = "START"
                continue                     # re-read this byte in START

        elif state == "STRING":
            if b is None or b == NEWLINE:
                raise CompileError(line, start_col, "string literal is not closed on its line")
            if b == QUOTE:                   # the closing '"' belongs to the literal
                tokens.append(Token("string", data[start:i].decode("latin-1"), line, start_col))
                state = "START"

        elif state == "MINUS":
            if b == GREATER:
                tokens.append(Token("operator", "->", line, start_col, "arrow"))
                state = "START"
            else:                            # a lone '-': the subtraction
                tokens.append(Token("operator", "-", line, start_col, "minus"))
                state = "START"
                continue                     # re-read this byte in START

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines
