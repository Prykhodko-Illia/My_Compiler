#!/usr/bin/env bash
# Runs every program in tests/ok and tests/err and compares the result with its .expected:
#   - tests/ok/NAME.txt must compile; the whole output of lli -- the lines of print and
#     the exit line -- must match NAME.expected. A program that reads input keeps it in
#     NAME.in, which becomes the standard input of lli; without one it reads nothing;
#   - tests/err/NAME.txt must fail; the error message must match NAME.expected,
#     and no output file may be written.
# When a NAME.ast file exists next to a program, the --ast dump must match it too; a
# NAME.ast-checked file is the same for --ast-checked, the tree after the semantic pass.
#
# Then every program in tests/opt, which must compile, runs twice -- without and with
# --remove-unused -- and both outputs must match NAME.expected: an optimization never changes
# what a program prints. NAME.astc is the tree the pass leaves, --remove-unused --ast-checked.
#
# Last, every program in tests/io is built as a native binary and run on its sessions
# (see that loop below).
#
# With --remove-unused (or REMOVE_UNUSED=1 in the environment) tests/ok, tests/err and
# tests/io are compiled through the pass too: every program must print the same, every
# error stay the same.
# The .ast and .ast-checked dumps are the trees without it, so they are compared as before.
#
# Usage (with the llvmlite venv active):  ./run_tests.sh [--remove-unused]

cd "$(dirname "$0")" || exit 2
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

flags=()
case "$1" in
    --remove-unused) flags=(--remove-unused) ;;
    "")              [ -n "$REMOVE_UNUSED" ] && [ "$REMOVE_UNUSED" != 0 ] && flags=(--remove-unused) ;;
    *)               echo "usage: ./run_tests.sh [--remove-unused]" >&2; exit 2 ;;
esac
[ ${#flags[@]} -gt 0 ] && echo "tests/ok and tests/err with --remove-unused"

passed=0
failed=0

fail() {
    failed=$((failed + 1))
    echo "FAIL  $1"
    shift
    for line in "$@"; do echo "      $line"; done
}

for src in tests/ok/*.txt tests/err/*.txt; do
    name=${src%.txt}
    case "$src" in
        tests/ok/*) must_compile=true ;;
        *)          must_compile=false ;;
    esac

    rm -f "$tmp/out.ll"
    if python3 compiler.py "${flags[@]}" "$src" "$tmp/out.ll" 2>"$tmp/stderr"; then
        $must_compile || { fail "$name" "it compiled, but it is in tests/err"; continue; }
        input=/dev/null
        [ -f "$name.in" ] && input="$name.in"
        actual=$(lli "$tmp/out.ll" < "$input" 2>&1)
    else
        $must_compile && { fail "$name" "it failed, but it is in tests/ok" "$(cat "$tmp/stderr")"; continue; }
        [ -f "$tmp/out.ll" ] && { fail "$name" "it failed but still wrote an output file"; continue; }
        actual=$(cat "$tmp/stderr")
    fi

    if [ ! -f "$name.expected" ]; then
        fail "$name" "no .expected file"
        continue
    fi
    if [ "$actual" != "$(cat "$name.expected")" ]; then
        fail "$name" "expected: $(cat "$name.expected")" "actual:   $actual"
        continue
    fi
    if [ -f "$name.ast" ] && [ "$(python3 compiler.py --ast "$src" 2>&1)" != "$(cat "$name.ast")" ]; then
        fail "$name" "the --ast dump differs from $name.ast:" \
             "$(diff <(python3 compiler.py --ast "$src" 2>&1) "$name.ast" | tr '\n' ' ')"
        continue
    fi
    if [ -f "$name.ast-checked" ] && [ "$(python3 compiler.py --ast-checked "$src" 2>&1)" != "$(cat "$name.ast-checked")" ]; then
        fail "$name" "the --ast-checked dump differs from $name.ast-checked:" \
             "$(diff <(python3 compiler.py --ast-checked "$src" 2>&1) "$name.ast-checked" | tr '\n' ' ')"
        continue
    fi

    passed=$((passed + 1))
    echo "PASS  $name"
done

# run NAME FLAG...: compile tests/opt/NAME.txt with the flags and print what lli prints;
# fails with the compiler's error when it does not compile.
run() {
    local name=$1
    shift
    rm -f "$tmp/out.ll"
    python3 compiler.py "$@" "$name.txt" "$tmp/out.ll" 2>"$tmp/stderr" || { cat "$tmp/stderr"; return 1; }
    local input=/dev/null
    [ -f "$name.in" ] && input="$name.in"
    lli "$tmp/out.ll" < "$input" 2>&1
}

for src in tests/opt/*.txt; do
    name=${src%.txt}
    if [ ! -f "$name.expected" ] || [ ! -f "$name.astc" ]; then
        fail "$name" "a program in tests/opt needs a .expected and a .astc file"
        continue
    fi
    expected=$(cat "$name.expected")
    plain=$(run "$name") || { fail "$name" "it failed without --remove-unused" "$plain"; continue; }
    optimized=$(run "$name" --remove-unused) || { fail "$name" "it failed with --remove-unused" "$optimized"; continue; }
    if [ "$plain" != "$expected" ]; then
        fail "$name" "without --remove-unused" "expected: $expected" "actual:   $plain"
        continue
    fi
    if [ "$optimized" != "$expected" ]; then
        fail "$name" "with --remove-unused" "expected: $expected" "actual:   $optimized"
        continue
    fi
    if [ "$(python3 compiler.py --remove-unused --ast-checked "$src" 2>&1)" != "$(cat "$name.astc")" ]; then
        fail "$name" "the --remove-unused --ast-checked dump differs from $name.astc:" \
             "$(diff <(python3 compiler.py --remove-unused --ast-checked "$src" 2>&1) "$name.astc" | tr '\n' ' ')"
        continue
    fi

    passed=$((passed + 1))
    echo "PASS  $name"
done

# Every program in tests/io is built as a native binary -- the compiler (with the flags),
# llc and clang, as in full_compiler.sh -- and fed each of its sessions NAME.S.in; what the
# binary prints must be exactly NAME.S.expected. The typed lines never reach the output.
for src in tests/io/*.txt; do
    [ -f "$src" ] || continue                   # an empty directory leaves the pattern itself
    name=${src%.txt}
    rm -f "$tmp/io.ll" "$tmp/io.o" "$tmp/io"
    if ! python3 compiler.py "${flags[@]}" "$src" "$tmp/io.ll" 2>"$tmp/stderr" \
        || ! llc -filetype=obj -relocation-model=pic "$tmp/io.ll" -o "$tmp/io.o" 2>>"$tmp/stderr" \
        || ! clang -fPIE "$tmp/io.o" -o "$tmp/io" 2>>"$tmp/stderr"; then
        fail "$name" "it does not build as a binary" "$(cat "$tmp/stderr")"
        continue
    fi
    sessions=("$name".*.in)
    if [ ! -f "${sessions[0]}" ]; then
        fail "$name" "a program in tests/io needs at least one NAME.S.in session"
        continue
    fi
    for input in "${sessions[@]}"; do
        session=${input%.in}
        if [ ! -f "$session.expected" ]; then
            fail "$session" "no .expected file"
            continue
        fi
        actual=$("$tmp/io" < "$input" 2>&1)
        if [ "$actual" != "$(cat "$session.expected")" ]; then
            fail "$session" "expected: $(cat "$session.expected")" "actual:   $actual"
            continue
        fi
        passed=$((passed + 1))
        echo "PASS  $session"
    done
done

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
