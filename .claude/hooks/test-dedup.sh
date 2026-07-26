#!/bin/bash
# Hard rule 2: dedup logic changes require a passing test.
# Runs the dedup suite immediately after any edit to dedup.py so failures surface
# in the same turn rather than three commits later.
# Exit 0 always: PostToolUse cannot undo the edit, so this reports rather than blocks.

command -v jq >/dev/null || { echo "guard-immutable.sh: jq not found on PATH — refusing to permit until dependency is fixed." >&2; exit 2; }

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$FILE" in
  *src/faers/dedup.py)
    echo "dedup.py changed — running tests/test_dedup.py"
    uv run pytest tests/test_dedup.py -q 2>&1 | tail -30
    ;;
esac

exit 0