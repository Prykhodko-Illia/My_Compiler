#!/usr/bin/env bash
# Compiles a source file to a native binary and runs it:  ./full_compiler.sh input.txt
# Leaves output.ll, output.o and program in the current directory.
set -euo pipefail

src=${1:?usage: ./full_compiler.sh <source>}
python3 "$(dirname "$0")/compiler.py" "$src" output.ll
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program
./program
