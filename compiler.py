"""Entry point:  python3 compiler.py <source> <output.ll>
          or:  python3 compiler.py --tokens <source>

source bytes --lexer--> tokens --parser--> AST --codegen--> str(module) -> output.ll
"""

import sys

from src.codegen import codegen
from src.errors import CompileError
from src.lexer import lex
from src.parser import end_of_input, parse


def report(e):
    print(f"compilation error: line {e.line}:{e.col}: {e.msg}", file=sys.stderr)
    sys.exit(1)


def read_source(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
        sys.exit(2)


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        print("usage: python3 compiler.py <source> <output.ll>\n"
              "       python3 compiler.py --tokens <source>", file=sys.stderr)
        sys.exit(2)

    if args[0] == "--tokens":                # debug: print the token stream, one source line per row
        data = read_source(args[1])
        try:
            token_lines = lex(data)
        except CompileError as e:
            report(e)
        for tokens in token_lines:
            print("  ".join(str(t) for t in tokens))
        return

    src_path, out_path = args
    data = read_source(src_path)
    try:
        token_lines = lex(data)
        module = codegen(parse(token_lines), end_of_input(token_lines))
    except CompileError as e:
        report(e)

    with open(out_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
