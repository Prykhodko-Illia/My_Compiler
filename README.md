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
There's no regex magic here and no borrowed lexer, no cap. A hand-written lexer pulls your source apart byte by byte,
the parser sorts the pieces, and the [llvmlite](https://github.com/numba/llvmlite) builder turns them into
LLVM IR. The only IR text that ever leaves this place is `str(module)`.

```
source bytes ──lexer──► tokens ──parser──► AST ──codegen (IRBuilder)──► output.ll ──llc + clang──► program
```

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
./full_compiler.sh tests/ok_pdf_example.txt      # Program exit with result 70
```

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

You get `PASS` or `FAIL` for each test, then a summary. If even one test fails, the script exits with a non-zero code.
To add a test, drop a `.txt` file and its `.expected` file into `tests/`.

---

## 📜 The language

One statement per line. Every variable is a 32-bit signed integer. That's the whole language, cuh.

| Statement | Example | What it does |
|---|---|---|
| const declaration | `i32 x{5}` | declares `x`; the initialiser in `{}` is mandatory |
| mut declaration | `i32 mut y{10}` | declares `y`, which can be reassigned; `mut` comes after the type name |
| assignment | `y := x + 3` | only a `mut` variable may be assigned |
| exit | `exit y` · `exit 42` | prints `Program exit with result <value>` and ends the program; must be the last statement |

The rules:

- An initialiser or the right-hand side of `:=` is a constant, a variable, or **one** operation (`+`, `-`, `*`)
  on two constants or variables: `{10}`, `{x}`, `{y+10}`, `{x * y}`.
- `exit` takes a single constant or variable.
- Constants are decimal digits with no sign, from `0` to `2147483647`.
- Names are ASCII letters, digits and `_`, and can't start with a digit. `i32`, `mut` and `exit` are reserved.
- A variable is declared once, before its first use, and can't appear in its own initialiser.
- Spaces and tabs separate tokens and are optional around `{`, `}`, `:=` and the operators. Blank lines are ignored.

Example:

```
i32 x{0}
i32 mut y{10}
i32 z{2+5}
i32 mut t { x + 10 }
t := t * z
exit t
```

```
Program exit with result 70
```

---

## 🚨 Caught lacking: errors

Break a rule and the compiler clocks you instantly. It prints one line to stderr, exits with code 1,
and writes no output file:

```
compilation error: line 2:1: cannot assign to 'x': it is not mut
```

`line:column` is the 1-based position of the first byte of the token that gave you away.

| Caught by | What gets you caught |
|---|---|
| lexer | an unknown byte (anything outside the language, including bytes above 127 and `\r`); a `{` not closed on the same line; a letter inside a number (`10x`); a `:` not followed by `=`; a lone `=` |
| parser | a line that isn't a declaration, an assignment or `exit`; a declaration without `{initialiser}`; more than one operation; extra tokens after a statement; a constant larger than 2147483647 |
| codegen | assigning to a variable that isn't `mut`; using a variable before its declaration; declaring a variable twice; a statement after `exit`; a program with no `exit` |

---

## 🗂️ What's in the repo

```
compiler.py       entry point: command-line arguments, --tokens, error reporting, writes output.ll
lexer.py          Token and lex(): a hand-written byte-by-byte state machine (START, IDENT, NUMBER, COLON)
parser.py         LineParser: turns each line of tokens into a statement node
ast_nodes.py      AST: Const, Var, BinOp, Declare, Assign, Exit
codegen.py        builds the IR with llvmlite.ir.IRBuilder, checks mut and declaration before use
errors.py         CompileError with line and column
tests/            ok_*.txt and err_*.txt programs with their .expected files
run_tests.sh      runs every test and compares it against its .expected file
full_compiler.sh  compiler.py + llc + clang + run, in one shot
```

### 🔦 How it works

1. **Lexer** (`lexer.py`): one loop reads the source one byte at a time, with an explicit state.
   No regular expressions, no `split()`, no lexer generator. A word is checked against the keyword table
   only once it is complete. Every token records its kind, text, line and column.
   The result is a list of lines, each a list of tokens.
2. **Parser** (`parser.py`): works on tokens only and never touches the source text. Each line is parsed on its own:
   `i32 [mut] name { expr }`, `name := expr` or `exit operand`, where `expr` is `operand [op operand]`.
3. **Code generation** (`codegen.py`): a `main` function with one `entry` block.
   - A declaration is an `alloca` plus a `store` of the initialiser.
   - A variable read is a `load`, and an operation is `add`, `sub` or `mul`.
   - An assignment is a `store`.
   - `exit` calls `printf` (declared, not defined; the linker finds it in libc) with a global format string, then `ret 0`.
   - A symbol table maps each name to its `alloca` and its `mut` flag.

---

> *No regex. No shortcuts. The lexer goes hard byte by byte, cuh.*
