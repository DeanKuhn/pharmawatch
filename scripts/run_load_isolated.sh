#!/usr/bin/env bash
# Run load.py in its own systemd scope so a kernel OOM-kill of the job is
# contained to that scope instead of cascading into the parent init.scope --
# which also holds bash and the running Claude Code session. See
# docs/personal/full_archive_load_crash_debugging.md for the crash pattern
# this guards against.
#
# MemoryMax is a hard cap (OOM-killed if exceeded); MemoryHigh throttles
# before that point. Both sit above DuckDB's own memory_limit pragma (4GB in
# load.py) to leave headroom for Python/Polars overhead within the job, while
# staying under total box RAM (7.7GB) so the rest of the session is never
# starved.
set -euo pipefail
exec systemd-run --user --scope -p MemoryHigh=4500M -p MemoryMax=5500M -- \
    python -m faers.load "$@"
