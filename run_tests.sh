#!/usr/bin/env bash
# Runs every program in tests/ok and tests/err and compares the result with its .expected:
#   - tests/ok/NAME.txt must compile; the output of lli must match NAME.expected;
#   - tests/err/NAME.txt must fail; the error message must match NAME.expected,
#     and no output file may be written.
# When a NAME.ast file exists next to a program, the --ast dump must match it too.
# Usage (with the llvmlite venv active):  ./run_tests.sh

cd "$(dirname "$0")" || exit 2
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

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
    if python3 compiler.py "$src" "$tmp/out.ll" 2>"$tmp/stderr"; then
        $must_compile || { fail "$name" "it compiled, but it is in tests/err"; continue; }
        actual=$(lli "$tmp/out.ll" 2>&1)
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

    passed=$((passed + 1))
    echo "PASS  $name"
done

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
