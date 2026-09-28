```
 ███╗   ███╗██╗   ██╗     ██████╗ ██████╗ ███╗   ███╗██████╗ ██╗██╗     ███████╗██████╗
 ████╗ ████║╚██╗ ██╔╝    ██╔════╝██╔═══██╗████╗ ████║██╔══██╗██║██║     ██╔════╝██╔══██╗
 ██╔████╔██║ ╚████╔╝     ██║     ██║   ██║██╔████╔██║██████╔╝██║██║     █████╗  ██████╔╝
 ██║╚██╔╝██║  ╚██╔╝      ██║     ██║   ██║██║╚██╔╝██║██╔═══╝ ██║██║     ██╔══╝  ██╔══██╗
 ██║ ╚═╝ ██║   ██║       ╚██████╗╚██████╔╝██║ ╚═╝ ██║██║     ██║███████╗███████╗██║  ██║
 ╚═╝     ╚═╝   ╚═╝        ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝
```

> *Pull the hood up. Put the text in. Get the machine code out, cuh.*

A compiler for a small integer language, built in Python for the *Languages and Compilers Design* course.
There's no regex magic here, no borrowed lexer and no parser generator, no cap. A hand-written lexer pulls your
source apart byte by byte, a recursive-descent parser builds a tree out of the pieces, and one walk over that tree
emits LLVM IR through the [llvmlite](https://github.com/numba/llvmlite) builder. The only IR text that ever
leaves this place is `str(module)`.

```
source bytes ──lexer──► tokens ──parser──► AST ──walk (IRBuilder)──► output.ll ──llc + clang──► program
```

The grammar the parser follows is written down in [`grammar.ebnf`](grammar.ebnf).

---

## 🕶️ Pull up with the right tools

Tested on Ubuntu 24.04 (a Multipass VM works fine).

```bash
sudo apt update
sudo apt install -y llvm clang python3 python3-venv python3-pip binutils file

python3 -m venv ~/lcd
source ~/lcd/bin/activate
pip install 'llvmlite==0.49.*'
```

Every new shell starts without the venv. Load it before you touch the compiler or the tests, cuh:

```bash
source ~/lcd/bin/activate
```

---

## ⚙️ Run the compiler

```bash
python3 compiler.py input.txt output.ll
```

Two arguments: the source to read and the `.ll` file to write. If everything checks out, you get `output.ll` and exit code 0.

Run the IR directly, no linker needed:

```bash
lli output.ll
```

Or go all the way to a native binary:

```bash
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program
./program
```

Don't want to type all that, cuh? `full_compiler.sh` does the whole chain in one shot and leaves
`output.ll`, `output.o` and `program` in the current directory:

```bash
./full_compiler.sh tests/ok_task4_example.txt      # Program exit with result 120
```

### 🌳 See the tree

`--ast` runs the lexer and the parser and prints the tree, one node per line, children indented by two spaces.
It writes no output file, so it is the fastest way to check what the parser understood:

```bash
printf 'i32 x{2 + 3 * 4}\nexit x\n' > p.txt
python3 compiler.py --ast p.txt
```

```
Program
  Decl x const
    BinOp +
      Const 2
      BinOp *
        Const 3
        Const 4
  Exit
    Var x
```

The `*` sits deeper than the `+`, so it is evaluated first: 14, not 20. Nobody wrote a precedence table for that —
it falls out of the shape of the grammar.

### 👁️ See what the lexer sees

`--tokens` runs the lexer only and dumps every token it finds, one source line per row,
as `(text, kind, subkind) line:column`:

```bash
printf '   i32 mut x{ 10 }\n    var t' > worked.txt
python3 compiler.py --tokens worked.txt
```

```
(i32, keyword, typename) 1:4  (mut, keyword, specifier) 1:8  (x, identifier) 1:12  ({, block, start) 1:13  (10, constant, numeric) 1:15  (}, block, end) 1:18  (\n, endline) 1:19
(var, identifier) 2:5  (t, identifier) 2:9
```

---

## 🧪 Run the tests, no cap

```bash
./run_tests.sh
```

Each `tests/NAME.txt` is a program, and `tests/NAME.expected` holds the result it must produce:

- `ok_*.txt` must compile. The script runs the IR with `lli` and compares the output with `NAME.expected`.
- `err_*.txt` must fail. The script compares the error message with `NAME.expected` and checks that
  no output file was left behind.
- If a `tests/NAME.ast` file exists, the `--ast` dump must match it as well.

You get `PASS` or `FAIL` for each test, then a summary. If even one test fails, the script exits with a non-zero code.
To add a test, drop a `.txt` file and its `.expected` file into `tests/`.

---

## 📜 The language

One statement per line. Every variable is a 32-bit signed integer. That's the whole language, cuh.

| Statement | Example | What it does |
|---|---|---|
| const declaration | `i32 x{5}` | declares `x`; the initialiser in `{}` is mandatory |
| mut declaration | `i32 mut y{10}` | declares `y`, which can be reassigned; `mut` comes after the type name |
| assignment | `y := x * 3 + 1` | only a `mut` variable may be assigned |
| exit | `exit y` · `exit 42` | prints `Program exit with result <value>` and ends the program; it is the last statement, and there is exactly one |

The rules:

- An initialiser and the right-hand side of `:=` are an expression: any chain of constants and variables
  joined by `+`, `-` and `*`, such as `{10}`, `{x}`, `{2 + 3 * 4}`, `y := a * b - c * 2`.
- `*` binds tighter than `+` and `-`; operators of equal precedence group from left to right.
  So `2 + 3 * 4` is 14, and `10 - 3 - 2` is `(10 - 3) - 2`, which is 5.
- `exit` takes a single constant or variable, never an operation.
- Constants are decimal digits with no sign, from `0` to `2147483647`.
- Names are ASCII letters, digits and `_`, and can't start with a digit. `i32`, `mut` and `exit` are reserved.
- A variable is declared once, before its first use, and can't appear in its own initialiser.
- Spaces and tabs separate tokens and are optional around `{`, `}`, `:=` and the operators. Blank lines are ignored.
- Not in the language, so each of these is a compilation error: parentheses; `/` or any operator other than
  `+`, `-`, `*`; a sign in front of a constant or a name (`-5`, `-x` — write `0 - 5`); two statements on one line;
  a declaration without `{}`; anything after `exit`.

Example:

```
i32 x{2 + 3 * 4}
i32 mut a{10 - 3 - 2}
a := a * 2 - 1
i32 z{x * a - 6}
exit z
```

```
Program exit with result 120
```

---

## 🚨 Caught lacking: errors

Break a rule and the compiler clocks you instantly. It prints one line to stderr, exits with code 1,
and writes no output file:

```
compilation error: line 2:1: cannot assign to 'x': it is not mut
```

`line:column` is the 1-based position of the first byte of the token that gave you away. When the line ends too
early (`x := x +`), the position is the column right after the last token — the spot where something should
have been typed.

| Caught by | What gets you caught |
|---|---|
| lexer | an unknown byte (anything outside the language, including `(`, `/`, bytes above 127 and `\r`); a `{` not closed on the same line; a letter inside a number (`10x`); a `:` not followed by `=`; a lone `=` |
| parser | a line that isn't a declaration, an assignment or `exit`; a declaration without `{initialiser}`; two operators in a row; an operator at the end of a line; extra tokens after a statement; a statement after `exit`; a program with no `exit`; a constant larger than 2147483647 |
| the walk | assigning to a variable that isn't `mut`; using a variable before its declaration; declaring a variable twice |

---

## 🗂️ What's in the repo

```
compiler.py         entry point: command-line arguments, --ast, --tokens, error reporting, writes output.ll
grammar.ebnf        the grammar of the language in EBNF, one rule per shape
src/
  lexer.py          Token and lex(): a hand-written byte-by-byte state machine (START, IDENT, NUMBER, COLON)
  parser.py         Parser: recursive descent, one method per grammar rule, builds the tree
  ast_nodes.py      the tree: Node, ProgramNode, DeclNode, AssignNode, ExitNode, BinOpNode, VarNode, ConstNode
  codegen.py        CodeGen: one walk over the tree that emits IR with llvmlite.ir.IRBuilder
  errors.py         CompileError with line and column
tests/              ok_*.txt and err_*.txt programs with their .expected files (and .ast files for some)
run_tests.sh        runs every test and compares it against its .expected (and .ast) file
full_compiler.sh    compiler.py + llc + clang + run, in one shot
```

### 🔦 How it works

1. **Lexer** (`src/lexer.py`): one loop reads the source one byte at a time, with an explicit state.
   No regular expressions, no `split()`, no lexer generator. A word is checked against the keyword table
   only once it is complete. Every token records its kind, text, line and column.
   The result is a list of lines, each a list of tokens.
2. **Parser** (`src/parser.py`): recursive descent, by hand. Two primitives do all the reading — `peek()` returns
   the current token, `eat()` returns it and moves on — and there is one method per rule of `grammar.ebnf`.
   One token of look-ahead decides every choice: `i32` starts a declaration, a name starts an assignment.
   It reads tokens only, never the source text, and puts no token into the tree.
   Precedence needs no table: `parse_expr` calls `parse_term` before it looks for a `+`, so everything a `*`
   glues together is already one node by the time the `+` is seen. Each loop puts what it has so far in as the
   left child, which is what makes the operators left-associative.
3. **The tree** (`src/ast_nodes.py`): a class hierarchy that mirrors the grammar — an abstract base per
   "either / or" rule, a class per shape. Quoted words disappear: `mut` becomes a boolean, `{}` and `:=` leave
   nothing behind. Every node keeps the line and column of the token it came from, so later phases can report
   positions with no tokens in sight. `dump()` prints the tree, and `accept(visitor)` sends a walk to the right method.
4. **The walk** (`src/codegen.py`): nothing calls the builder until the whole tree stands, then `CodeGen` walks it
   with one method per node kind, into a `main` function with one `entry` block.
   - A declaration is an `alloca` plus a `store` of the initialiser.
   - A variable read is a `load`, and an operation asks its children for their values before emitting `add`,
     `sub` or `mul` — operands first, bottom-up.
   - An assignment is a `store`.
   - `exit` calls `printf` (declared, not defined; the linker finds it in libc) with a global format string, then `ret 0`.
   - A symbol table maps each name to its `alloca`, its `mut` flag and where it was declared, which is where the
     three semantic checks live: declared before use, declared once, and `mut` before `:=`.

Every later phase is another walk over this same tree, and none of them touches the parser.

---

> *No regex. No shortcuts. The lexer goes hard byte by byte, cuh.*
