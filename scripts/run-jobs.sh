#!/usr/bin/env bash
# run-jobs.sh — run configured cortex jobs now, by id or glob.
# Usage: ./scripts/run-jobs.sh [--list] [--parallel N] [--no-email] [--include-disabled] JOB|'GLOB' ...
#
# Pipes scripts/run_jobs.py from the host into the RUNNING cortex container,
# so it needs no rebuild. Parallel (10) by default; event-watch-* takes as
# long as its slowest scraper (~13 min). Detach with
#   setsid nohup ./scripts/run-jobs.sh 'event-watch-*' > /tmp/watch-run.log 2>&1 < /dev/null &
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

exec docker compose exec -T cortex python -u - "$@" < "$SCRIPT_DIR/run_jobs.py"
