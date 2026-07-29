#!/bin/bash
# Hard rule 1: never mutate raw data.
# Blocks any Write or Edit targeting the immutable zones or the secrets file.
# Exit 2 blocks the tool call; stderr is fed back to Claude as the reason.

command -v jq >/dev/null || { echo "guard-immutable.sh: jq not found on PATH — refusing to permit until dependency is fixed." >&2; exit 2; }

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE" ]; then
  exit 0
fi

case "$FILE" in
  *data/raw/*|*data/parquet/*)
    echo "Blocked by hard rule 1: $FILE is in an immutable zone. Raw downloads and parsed Parquet are read-only once written. Write derived output somewhere else." >&2
    exit 2
    ;;
  *.env)
    echo "Blocked: .env holds the Neon connection string and is not committed. Do not read or modify it." >&2
    exit 2
    ;;
esac

exit 0