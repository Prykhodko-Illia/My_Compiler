"""Entry point:  python3 compiler.py [--remove-unused] <source> <output.ll>
          or:  python3 compiler.py --ast <source>
          or:  python3 compiler.py [--remove-unused] --ast-checked <source>
          or:  python3 compiler.py --tokens <source>
The flags may stand anywhere on the command line.

source bytes --lexer--> tokens --parser--> AST --semantic pass--> typed AST
            [--remove-unused--> smaller typed AST] --walk (codegen)--> str(module) -> output.ll

Nothing is built until the semantic pass has approved the whole tree.
"""

import sys

from src.codegen import codegen
from src.errors import CompileError
from src.lexer import lex
from src.optimizer import remove_unused
from src.parser import parse
from src.semantic import check


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


USAGE = ("usage: python3 compiler.py [--remove-unused] <source> <output.ll>\n"
         "       python3 compiler.py --ast <source>\n"
         "       python3 compiler.py [--remove-unused] --ast-checked <source>\n"
         "       python3 compiler.py --tokens <source>")
MODES = ("--tokens", "--ast", "--ast-checked")


def usage():
    print(USAGE, file=sys.stderr)
    sys.exit(2)


def main():
    # The flags may stand anywhere: first, last, or between the paths.
    flags = [arg for arg in sys.argv[1:] if arg.startswith("--")]
    paths = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    modes = [flag for flag in flags if flag in MODES]
    if any(flag not in (*MODES, "--remove-unused") for flag in flags) or len(modes) > 1:
        usage()
    mode = modes[0] if modes else None
    optimize = "--remove-unused" in flags
    if len(paths) != (1 if mode else 2):
        usage()

    def checked(data):
        # The pass runs only on a tree the checker has accepted: an optimization never
        # sees an invalid program.
        program = check(parse(lex(data)))
        return remove_unused(program) if optimize else program

    if mode == "--tokens":                   # debug: the token stream, one source line per row
        data = read_source(paths[0])
        try:
            token_lines = lex(data)
        except CompileError as e:
            report(e)
        for tokens in token_lines:
            print("  ".join(str(t) for t in tokens))
        return

    if mode == "--ast":                      # debug: the parse tree, writing no output file
        data = read_source(paths[0])
        try:
            program = parse(lex(data))
        except CompileError as e:
            report(e)
        print(program.dump(), end="")
        return

    if mode == "--ast-checked":              # debug: the tree code generation would get
        data = read_source(paths[0])
        try:
            program = checked(data)
        except CompileError as e:
            report(e)
        print(program.dump(), end="")
        return

    src_path, out_path = paths
    data = read_source(src_path)
    try:
        # lex -> parse -> check (types, names) -> [remove unused] -> emit: nothing is built
        # until the semantic pass has approved the whole tree.
        module = codegen(checked(data))
    except CompileError as e:
        report(e)

    with open(out_path, "w") as f:
        f.write(str(module))


if __name__ == "__main__":
    main()
