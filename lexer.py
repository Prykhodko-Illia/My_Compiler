"""Phase 0: bytes -> tokens  (lexical errors only)

A hand-written state machine: one loop, one byte per step. The state says
what we are in the middle of (nothing, a word, a number, a ':'), the byte's
class says what happens next. A byte that ends a token is read again in
START (`continue` without advancing) -- the only byte ever re-examined.
"""

from dataclasses import dataclass

from errors import CompileError, error_at


@dataclass
class Token:
    kind: str        # keyword | identifier | constant | block | operator | endline
    text: str
    line: int
    col: int         # 1-based position of the token's first byte
    sub: str = ""    # typename | specifier | statement | numeric | start | end | assign | plus | minus | times

    def __str__(self):
        text = "\\n" if self.kind == "endline" else self.text
        fields = ", ".join(f for f in (text, self.kind, self.sub) if f)
        return f"({fields}) {self.line}:{self.col}"


KEYWORDS = {b"i32": "typename", b"mut": "specifier", b"exit": "statement"}

SINGLE_BYTE = {                  # tokens that are complete after one byte
    ord("{"): ("block", "start"),
    ord("}"): ("block", "end"),
    ord("+"): ("operator", "plus"),
    ord("-"): ("operator", "minus"),
    ord("*"): ("operator", "times"),
}

SPACE, TAB, NEWLINE = 32, 9, 10
COLON, EQUALS, LBRACE, RBRACE = ord(":"), ord("="), ord("{"), ord("}")


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
    open_braces = []                         # '{' tokens not yet closed on this line
    state, start, start_col = "START", 0, 1
    line, col = 1, 1
    i = 0
    while i <= len(data):                    # one extra step: the end of input
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None or b == NEWLINE:
                if open_braces:
                    raise error_at(open_braces[-1], "'{' is not closed before the end of the line")
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
            elif b in SINGLE_BYTE:
                kind, sub = SINGLE_BYTE[b]
                token = Token(kind, chr(b), line, col, sub)
                tokens.append(token)
                if b == LBRACE:
                    open_braces.append(token)
                elif b == RBRACE and open_braces:
                    open_braces.pop()        # a '}' with no '{' is the parser's problem
            elif b == EQUALS:
                raise CompileError(line, col, "unexpected byte '=': assignment is ':='")
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

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines
