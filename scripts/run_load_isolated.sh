#!/usr/bin/env bash
# Run load.py in its own systemd scope so a kernel OOM-kill of the job is
# contained to that scope instead of cascading into the parent init.scope --
# which also holds bash and the running Claude Code session. See
# docs/personal/full_archive_load_crash_debugging.md for the crash pattern
# this guards against.
#
# MemoryMax is a hard cap (OOM-killed if exceeded); MemoryHigh throttles
# before that point. Both are derived from the VM's actual MemTotal rather
# than hardcoded, because a cap above total RAM cannot ever fire: on
# 2026-07-31 a 10500M cap on a 7.67GB VM let the kernel's global OOM killer
# take the job first, with the scope's own limit never reached. The same
# reasoning governs DuckDB's memory_limit in load.py::duckdb_memory_limit_mb.
set -euo pipefail

total_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
high_mb=$((total_kb * 70 / 100 / 1024))
max_mb=$((total_kb * 80 / 100 / 1024))

echo "scope caps: MemoryHigh=${high_mb}M MemoryMax=${max_mb}M (MemTotal=$((total_kb / 1024))M)" >&2

exec systemd-run --user --scope \
    -p "MemoryHigh=${high_mb}M" -p "MemoryMax=${max_mb}M" -- \
    uv run python -m faers.load "$@"
