#!/usr/bin/env bash
# Runs every tests/*.txt through the compiler and compares the result with tests/*.expected:
#   - if the compiler succeeds, the program's output (via lli) must match;
#   - if it fails, its stderr must match, and no output file may be written.
# Usage (with the llvmlite venv active):  ./run_tests.sh

cd "$(dirname "$0")" || exit 2
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

passed=0
failed=0
for src in tests/*.txt; do
    name=${src%.txt}
    rm -f "$tmp/out.ll"

    if python3 compiler.py "$src" "$tmp/out.ll" 2>"$tmp/stderr"; then
        actual=$(lli "$tmp/out.ll" 2>&1)
    elif [ -f "$tmp/out.ll" ]; then
        actual="(compiler failed but wrote an output file)"
    else
        actual=$(cat "$tmp/stderr")
    fi

    if [ -f "$name.expected" ] && [ "$actual" == "$(cat "$name.expected")" ]; then
        passed=$((passed + 1))
        echo "PASS  $name"
    else
        failed=$((failed + 1))
        echo "FAIL  $name"
        echo "      expected: $(cat "$name.expected" 2>/dev/null || echo '(no .expected file)')"
        echo "      actual:   $actual"
    fi
done

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
