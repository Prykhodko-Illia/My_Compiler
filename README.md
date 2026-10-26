```
 ███╗   ███╗██╗   ██╗     ██████╗ ██████╗ ███╗   ███╗██████╗ ██╗██╗     ███████╗██████╗
 ████╗ ████║╚██╗ ██╔╝    ██╔════╝██╔═══██╗████╗ ████║██╔══██╗██║██║     ██╔════╝██╔══██╗
 ██╔████╔██║ ╚████╔╝     ██║     ██║   ██║██╔████╔██║██████╔╝██║██║     █████╗  ██████╔╝
 ██║╚██╔╝██║  ╚██╔╝      ██║     ██║   ██║██║╚██╔╝██║██╔═══╝ ██║██║     ██╔══╝  ██╔══██╗
 ██║ ╚═╝ ██║   ██║       ╚██████╗╚██████╔╝██║ ╚═╝ ██║██║     ██║███████╗███████╗██║  ██║
 ╚═╝     ╚═╝   ╚═╝        ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝
```

> *Pull the hood up. Put the text in. Get the machine code out, cuh.*

A compiler for a small typed language, built in Python for the *Languages and Compilers Design* course.
There's no regex magic here, no borrowed lexer and no parser generator, no cap. A hand-written lexer pulls your
source apart byte by byte, a recursive-descent parser builds a tree out of the pieces, a semantic pass types that
tree and throws it out if it lies, and only then does one last walk emit LLVM IR through the
[llvmlite](https://github.com/numba/llvmlite) builder. The only IR text that ever leaves this place is `str(module)`.

```
source bytes ──lexer──► tokens ──parser──► AST ──semantic pass──► typed AST ──walk (IRBuilder)──► output.ll ──llc + clang──► program
```

Since Practice 5 the language branches and loops: `if` / `else` and `while` with blocks, and `!`. A block of the source is a **scope** in the
semantic pass and a run of **basic blocks** in the IR — two different things, kept apart on purpose.
Since Practice 6 it has **structs** (your own types, with fields) and **functions** (with recursion), and a program
has a fixed shape: structs, then functions, then statements, then `exit`.

The grammar the parser follows is written down in [`grammar.ebnf`](grammar.ebnf).
Nothing reaches the builder until the pass has approved the whole program.

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

Want to see what LLVM does with your slots? `mem2reg` promotes the entry-block `alloca`s to registers, and
where a variable was assigned in both arms of an `if`, a `phi` shows up in the merge block:

```bash
opt -passes=mem2reg -S output.ll
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
./full_compiler.sh tests/ok/task1_program.txt      # Program exit with result 385
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
  Decl x i32 const
    BinOp +
      Const 2
      BinOp *
        Const 3
        Const 4
  Exit
    Var x
```

The `*` sits deeper than the `+`, so it is evaluated first: 14, not 20. Nobody wrote a precedence table for that —
it falls out of the shape of the grammar. The declaration line shows the type it was declared with, so
`i64 mut y{x}` dumps as `Decl y i64 mut` and `bool d{a == 10}` as `Decl d bool const` over a `BinOp ==`.

An `if` dumps as `If` over its condition, then one `Block` per arm; `!` dumps as `Not`:

```
If
  Not
    Var c
  Block
    Assign a
      Const 1
  Block
    Exit
      Var a
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

Every program lives in one of two folders, and `NAME.expected` next to it holds the result it must produce:

- `tests/ok/NAME.txt` must compile. The script runs the IR with `lli` and compares the output with `NAME.expected`.
- `tests/err/NAME.txt` must fail. The script compares the error message with `NAME.expected` and checks that
  no output file was left behind.
- If a `NAME.ast` file sits next to a program, the `--ast` dump must match it as well.

New programs are named after the feature they exercise, `feature_variant`: `if-else_nested.txt`,
`scope_shadow-type.txt`, `not_on-integer.txt`.

A program in the wrong folder is a failure by itself: an `ok` program that doesn't compile, or an `err`
program that does, is reported as such. You get `PASS` or `FAIL` for each test, then a summary; if even one
test fails, the script exits with a non-zero code. To add a test, drop a `.txt` and a `.expected` into the
folder that matches what it should do.

---

## 📜 The language

One statement per line, and an `if` spreads over several. Three built-in types: `i32` and `i64` are integers,
`bool` is a boolean whose only literals are `true` and `false`. On top of them, your own structs. That's the whole
language, cuh.

A program comes in a fixed order — **structs, then functions, then statements, then `exit`**:

```
struct Point                    fn moved := (Point p, i32 dx) -> Point
{                               {
    i32 mut x                       Point q{p.x + dx, p.y}
    i32 mut y                       exit q
}                               }
struct Circle
{                               Point p{10, 20}
    Point center                Circle c{p, 5}
    i32 mut radius              Point mut r{moved(p, 1)}
}                               r := moved(r, 2)
                                exit r.x                   → Program exit with result 13
```

| Statement | Example | What it does |
|---|---|---|
| const declaration | `i32 x{5}` · `bool b{true}` | declares `x`; the initialiser in `{}` is mandatory |
| mut declaration | `i64 mut y{10}` | declares `y`, which can be reassigned; `mut` comes after the type name |
| object declaration | `Point p{10, 20}` · `Point q{p}` | one value per field, in field order — or one whole object of the same struct, which is copied |
| assignment | `y := x * 3 + 1` · `c.radius := 5` | writes a variable, a field, or a whole object; see the `mut` rules below |
| if / else | see below | runs the first block when the `bool` condition is true, else the second; `else` is optional |
| while | see below | runs the block again and again while the `bool` condition is true |
| exit | `exit y` · `exit 42` · `exit !b` · `exit fib(10)` | at the top level: prints `Program exit with result <value>` and ends the program. In a function: ends the function, and its value is the result. It is the last line of its block; the program and every function end with one |

```
if b                        while i != 11
{                           {
    a := a + 5                  sum := sum + i
}                               i := i + 1
else                        }
{
    a := a - 5
}
```

- `if` / `while` and the condition stand on one line; `{`, `}` and `else` stand alone on theirs. A block holds at
  least one line.
- Blocks nest: an `if` and a `while` are statements, so they may stand inside a block.
- A block may end with `exit`, as its last line. The program still ends with an `exit` of its own.
- A block is a scope. A name declared inside is gone after the `}`, and a block may declare a name that already
  exists outside it, with any type — that's shadowing. A name means the innermost declaration of it.

**Structs**

- `struct Name`, then `{`, one field per line (`Type [mut] name`), then `}`. At least one field.
- A field's type is built in, or a struct declared **above** — so a struct can never contain itself.
- A field is reached with `.`, at any depth: `c.center.x`. A field chain can stand wherever a variable can,
  and to the left of `:=`.
- A field without `mut` gets its value only when the object is created. So:
  - writing through `x.a.b` needs **every link** to be `mut` — the variable, `a` and `b`;
  - `:=` on a whole object (or on a struct-typed field) needs every field inside it, at every depth, to be `mut`.
- A struct field can get its own `{}` inside the outer one, at any depth, with the same rules — one value per
  field, in order, or one whole object: `Circle c{{10, 20}, 5}`, `Ring g{{{10, 20}, r}, {{10, 20}, 2}}`. A `{}`
  where a built-in value belongs (`Point p{{1, 2}, 3}`) is an error at that `{`.
- Objects are values: `Point q{p}` copies `p`, and so does passing it to a function or returning it. Changing
  `p` afterwards leaves `q` alone.
- An object can't be printed by `exit`, compared with `==`, or used in arithmetic.

**Functions**

```
fn fib := (i32 n) -> i32
{
    if n == 0
    {
        exit 0
    }
    if n == 1
    {
        exit 1
    }
    i32 result{fib(n - 1) + fib(n - 2)}
    exit result
}
exit fib(10)
```

- `fn name := (Type a, Type b, ...) -> Type`, then a block whose **last line is `exit`**. Any number of
  parameters, including none. Parameter and result types are built in or structs.
- `exit` in a function ends it, and its value is the result — of the result type (an `i32` may widen into an
  `i64` result). An `exit` inside an `if` or a `while` ends the function early.
- Parameters are copies and can't be changed, not even a field of one. The body sees its parameters, its own
  variables and every function — **never a top-level variable**.
- A function can call any function of the program: itself, or one declared below it.
- A call `f(a, b)` can stand wherever a value can, with one argument per parameter (an `i32` may go into an
  `i64` parameter). A call alone on a line isn't a statement. Functions and structs are declared only at the top
  level, never inside a block.

The rules:

- An initialiser and the right-hand side of `:=` are an expression: any chain of constants and variables
  joined by `+`, `-` and `*`, optionally compared with `==` or `!=`.
- `*` binds tighter than `+` and `-`; operators of equal precedence group from left to right.
  So `2 + 3 * 4` is 14, and `10 - 3 - 2` is `(10 - 3) - 2`, which is 5.
- A comparison binds weaker than all of it and gives a `bool`, so `x * 2 != y + 5` compares the two results.
  One comparison per expression.
- `!` negates a `bool` and binds tighter than everything: it applies to the factor right after it, so `!a == b`
  compares `!a` with `b`. Spaces after `!` are optional.
- The condition of an `if` or a `while` is a `bool`.
- Arithmetic works on integers only, and the result takes the wider of the two types: `i32 + i64` is `i64`.
- `==` and `!=` compare two integers (any mix of widths) or two bools.
- **An `i32` value may go into an `i64` variable, and nothing else converts**: an `i64` never goes back into an
  `i32`, and a `bool` never meets an integer.
- A constant takes the narrowest type it fits: `10` is `i32`, `3000000000` is `i64`, and anything past
  `9223372036854775807` is an error.
- `exit` takes a single constant, variable, field chain or call — optionally under `!` — never an operation. At
  the top level it must be an integer or a bool.
- Names are ASCII letters, digits and `_`, and can't start with a digit. `i32`, `i64`, `bool`, `mut`, `exit`,
  `if`, `else`, `while`, `struct`, `fn`, `true` and `false` are reserved. Functions have names of their own:
  a variable and a function may share one, since `f(...)` and `f` can't be confused.
- A variable is declared once per block, before its first use, and can't appear in its own initialiser
  (in a block that shadows it, the initialiser reads the outer one).
- Spaces and tabs separate tokens and are optional around `{`, `}`, `:=` and the operators. Blank lines are ignored.
- Not in the language, so each of these is a compilation error: grouping parentheses (they exist only in a
  function's signature and in calls); `/`, `<`, `>` or any operator other than `+ - * == != !`; a lone `=`; a
  sign in front of a constant or a name (`-5`, `-x` — write `0 - 5`);
  two comparisons in one expression; two statements on one line; a declaration without `{}`; `{` on the `if` or
  `while` line;
  an empty block; anything after an `exit` in its block.

Example:

```
i64 a{10}
bool b{true}
bool d{a == 10}
i32 x{15}
i64 mut y{x}
i64 z{x + 10}
y := y * z + a
exit y
```

```
Program exit with result 385
```

`exit` of a bool prints the word: `bool b{false}` then `exit b` gives `Program exit with result false`.

And all of it together — the Practice 6 example, in [`tests/ok/program_task3-example.txt`](tests/ok/program_task3-example.txt):

```
struct Point                         fn moved := (Point p, i32 dx) -> Point
{                                    {
    i32 mut x                            Point q{p.x + dx, p.y}
    i32 mut y                            exit q
}                                    }
struct Circle                        Point p{10, 20}
{                                    Circle c{p, 5}
    Point center                     i32 xCoord{c.center.x}
    i32 mut radius                   bool z{isXZero(p)}
}                                    Point mut r{moved(p, 1)}
fn add := (i32 a, i32 b) -> i32      r := moved(r, 2)
{                                    i32 result{add(xCoord, add(r.x, c.radius))}
    i32 sum{a + b}                   exit result
    exit sum
}                                    Program exit with result 28
fn isXZero := (Point p) -> bool
{
    bool zero{p.x == 0}
    exit zero
}
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
| lexer | an unknown byte (anything outside the language, including `/`, `<`, `>` on its own, bytes above 127 and `\r`); a letter inside a number (`10x`); a `:` not followed by `=`; a lone `=` |
| parser | a struct after a function, or a struct or function after a statement (or inside a block); a struct with no fields; a function body that doesn't end with `exit`; a call alone on a line; a missing `,` or `)` in a signature or a call; a line that isn't a declaration, an assignment, an `if`, a `while` or `exit`; a declaration without `{initialiser}` or with its `}` missing; two operators in a row; an operator at the end of a line; two comparisons in one expression; extra tokens after a statement, including `{` on the `if` or `while` line; no `{` line after `if`, `else` or `while`; an empty block; a `{` that is never closed; `else` without an `if`; a statement after `exit` in the same block; a program with no `exit` |
| semantic pass | every type rule — a `bool` in arithmetic, a `bool` compared with an integer, an `i64` value narrowing into an `i32`, a `bool` and an integer meeting across `{}` or `:=`, a constant too large for the type it lands in, an `if` or `while` condition that isn't a `bool`, `!` on an integer — plus using a variable before its declaration or after its block, declaring one twice in the same block, and assigning to one that isn't `mut`. Structs: an unknown type, a struct that contains itself, a duplicate struct or field, the wrong number of values in `{}` (at any depth), a `{}` for a built-in field or variable, a field that doesn't exist, a write through a link that isn't `mut`, `:=` on an object with a const field inside, comparing objects, `exit` of an object at the top level. Functions: an undeclared function, the wrong number or types of arguments, an `exit` of the wrong type, a duplicate function or parameter, assigning to a parameter, a top-level variable used in a function |
| the walk | nothing. By the time it runs, the tree has been checked |

---

## 🗂️ What's in the repo

```
compiler.py         entry point: command-line arguments, --ast, --tokens, error reporting, writes output.ll
grammar.ebnf        the grammar of the language in EBNF, one rule per shape
src/
  lexer.py          Token and lex(): a hand-written byte-by-byte state machine (START, IDENT, NUMBER, COLON, EQ, BANG, MINUS)
  parser.py         Parser: recursive descent with a line cursor and a token cursor, one method per grammar rule
  ast_nodes.py      the tree: Node, ProgramNode, StructNode, MemberNode, FnNode, ParamNode, DeclNode, AssignNode,
                    IfNode, WhileNode, BlockNode, ExitNode, BinOpNode, NotNode, VarNode, FieldNode, CallNode,
                    BoolNode, ConstNode
  semantic.py       SemanticChecker: the walk that owns the scope stack, the struct and function tables, types
                    every expression and rejects the rest
  codegen.py        CodeGen: the walk that emits IR with llvmlite.ir.IRBuilder — functions, named struct types, gep,
                    call, basic blocks, cbranch, sext, icmp, select
  errors.py         CompileError with line and column
tests/ok/           programs that must compile, with their .expected output (and .ast files for some)
tests/err/          programs that must fail, with their .expected error message
run_tests.sh        runs every test and compares it against its .expected (and .ast) file
full_compiler.sh    compiler.py + llc + clang + run, in one shot
```

### 🔦 How it works

1. **Lexer** (`src/lexer.py`): one loop reads the source one byte at a time, with an explicit state.
   No regular expressions, no `split()`, no lexer generator. A word is checked against the keyword table
   only once it is complete. Every token records its kind, text, line and column.
   `!` and `-` are the states with two ways out: `=` makes `!=` and `>` makes `->`, anything else makes a lone
   `!` or `-` and is read again.
   `{` and `}` are plain tokens — the lexer doesn't pair them, the parser does.
   The result is a list of lines, each a list of tokens.
2. **Parser** (`src/parser.py`): recursive descent, by hand, with two cursors. The line cursor walks the source:
   `peek_line()` looks at the first token of the next non-blank line, `next_line()` makes it current. The token
   cursor walks that line: `peek()` returns the current token, `eat()` returns it and moves on. There is one method
   per rule of `grammar.ebnf`. A declaration is one line; an `if` (or a `while`) reads its condition line, the
   `{` line, every line of the block and the `}` line — and the program and every block are read by the same loop, which is
   why blocks nest for free. The program itself is read in order: structs while the next line starts with
   `struct`, functions while it starts with `fn`, then statements — so a struct or a function out of place reaches
   the statement parser, which reports it at that line. One token of look-ahead decides almost every choice:
   `i32` starts a declaration, `if` an if, a name followed by `(` a call and by `.` a field chain. The exception is
   a line that starts with a name: `Point p{…}` is a declaration, `p := …` an assignment, so there the parser
   looks at the second token — a name or `mut` means declaration.
   It reads tokens only, never the source text, and puts no token into the tree.
   Precedence needs no table: `parse_expr` calls `parse_term` before it looks for a `+`, so everything a `*`
   glues together is already one node by the time the `+` is seen. Each loop puts what it has so far in as the
   left child, which is what makes the operators left-associative.
3. **The tree** (`src/ast_nodes.py`): a class hierarchy that mirrors the grammar — an abstract base per
   "either / or" rule, a class per shape. Quoted words disappear: `mut` becomes a boolean, `{}` and `:=` leave
   nothing behind. Every node keeps the line and column of the token it came from, so later phases can report
   positions with no tokens in sight. `dump()` prints the tree, and `accept(visitor)` sends a walk to the right method.
4. **The semantic pass** (`src/semantic.py`): a walk of its own, over the whole tree, before any IR exists — so a
   bad program is rejected without a single instruction being built, and the pass can be tested without LLVM at all.
   It owns the symbol table and answers the question the parser cannot: what type is this expression?
   The symbol table is a stack of frames (name → declaration, which knows its type and `mut` flag). A block pushes
   an empty frame and pops it at its `}`; a declaration goes into the top frame and is checked for a duplicate
   there only, so an inner block may shadow an outer name with any type; a use walks the frames from the top down
   and takes the first hit. It leaves two fields behind, and that is its entire contract with the generator:
   `node.type` on every expression, `node.decl` on every name — so three `x` in three scopes are three
   declarations, and the generator never has to find out which is which. Everything it enforces — the type
   rules, a `bool` condition, a `bool` under `!`, declared before use, declared once per block, `mut` before
   `:=` — lives here and nowhere else.
   - **Structs** go into a struct table in program order, so a field can only name a struct declared above. A
     field chain like `c.center.x` is walked link by link — each link must exist, and for a write each must be
     `mut` — and turned into **field numbers** stored on the node (`node.indices = [0, 0]`). The generator never
     sees a field name.
   - **Functions** are checked in two rounds: every signature first, then every body — which is what lets a
     function call itself or one declared below it. A body gets a fresh scope stack whose bottom frame holds the
     parameters, so a top-level variable simply isn't on it. A call checks its arguments against the parameters
     and gets the result type; `exit` in a body checks against the result type. Calls carry `node.fn`.
5. **The walk that emits** (`src/codegen.py`): `CodeGen`, one method per node kind. It trusts the tree and checks
   nothing.
   - Every `fn` becomes a function of the module, named with a prefix (`@fn_fib`), so a function called `main` or
     `printf` can't clash with the real ones. All of them are declared before any body is built, so a `call` can
     point at a body that doesn't exist yet. Each function gets its own `entry` block and builder; its parameters
     are stored into slots there, so the body reads them with a plain `load`. The top-level statements become
     `main`.
   - A struct is a named type, `%"Point" = type {i32, i32}`, and an object is one `alloca` of it. A field is reached
     with `getelementptr`: `0` to step through the pointer, then one index per link — `c.center.x` is
     `gep %c, 0, 0, 0`. A whole object moves as one value: one `load` of `%"Point"` and one `store`, which is how
     `Point q{p}` copies, and how objects are passed to and returned from functions.
   - An inner `{}` (`Circle c{{10, 20}, 5}`) is not an object in memory but one value of the struct type, built in
     registers: it starts as `undef`, and each field goes in with `insertvalue` by index — recursively for a
     `{}` inside it. The finished value is stored into its field through the outer `gep`.
   - A declaration is an `alloca` of the declared type plus a `store` of the initialiser. The `alloca` always goes
     to the top of the `entry` block, whatever block the builder is in: a slot made inside `then` wouldn't exist
     on the `else` path, and `mem2reg` only promotes entry-block allocas. The `store` stays where it is.
   - An `if` appends `then`, `else` (when there is one) and `merge` blocks to the function and ends the current
     block with a `cbranch` on the `i1` of the condition — to two block objects, never labels as text. Each arm
     gets a `br` to `merge`, unless the block the builder ended up in is already terminated: an `exit` inside
     an arm has its `ret`, and a block takes exactly one terminator. When both arms exit, `merge` has no
     predecessor and still gets what follows the `if`.
   - A `while` is three blocks: `cond`, which the loop comes back to, `body` and `end`. The current block jumps to
     `cond`, `cond` does a `cbranch` to `body` or `end`, and `body` ends with `br cond` — the back edge, the only
     branch in the IR that points backwards (unless an `exit` in the body already ended it). `mem2reg` turns the
     loop variables into `phi`s at the top of `cond`. A slot declared in the body is still one `alloca` in
     `entry`, not a new one on every pass.
   - `!` is `xor i1 %v, true` (`builder.not_`).
   - A variable read is a `load`; an operation asks its children for their values before emitting `add`,
     `sub` or `mul` — operands first, bottom-up.
   - The one implicit conversion of the language is explicit here: `coerce()` emits `sext i32 … to i64` at the five
     places the pass allows widening — an initialiser, an assignment, the operands of `+ - *`, the operands of
     `== !=`, and the `exit` value. Leave one out and the builder refuses the instruction on the spot.
   - A comparison is `icmp` with a signed predicate on two operands of the same width, producing the `i1` that a
     `bool` variable stores.
   - `exit` in a function is `ret` of its value. At the top level it calls `printf` (declared, not defined; the
     linker finds it in libc): an integer is widened to `i64` and printed with `%lld`, a bool picks between two
     string constants with a `select` and prints with `%s`. Then `ret 0`. Either way it ends the block it stands
     in, so an `exit` in an arm terminates that arm's block.
   - A call is `call` with each argument coerced to its parameter's type.

Every later phase is another walk over this same tree, and none of them touches the parser.

---

> *No regex. No shortcuts. The lexer goes hard byte by byte, cuh.*
