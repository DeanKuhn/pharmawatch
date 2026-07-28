#!/bin/bash
# Formats Python files after Claude edits them, so formatting never needs to be
# a CLAUDE.md instruction. Exit 0 always — formatting failure should not block work.

command -v jq >/dev/null || { echo "guard-immutable.sh: jq not found on PATH — refusing to permit until dependency is fixed." >&2; exit 2; }

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$FILE" in
  *.py)
    uv run ruff format "$FILE" 2>/dev/null
    uv run ruff check --fix "$FILE" 2>/dev/null
    ;;
esac

exit 0