#!/usr/bin/env bash
#
# Hermetic test runner for ops fleet / host use (no Docker required).
# Prefer `make test` (docker) for the full project workflow; this script is the
# ops-worker contract: ./run_tests.sh [pytest args...]
#
# Live network tests stay skipped unless you pass --live or set RUN_LIVE_TESTS=1.
#
#   ./run_tests.sh
#   ./run_tests.sh -m smoke
#   ./run_tests.sh deps
#
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

VENV=".venv"
PYTHON="${PYTHON:-python3}"
STAMP="$VENV/.deps-installed"

force_deps=0
if [ "${1:-}" = "deps" ]; then
  force_deps=1
  shift
fi

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c '' 2>/dev/null; then
  echo ">> creating venv at $VENV"
  "$PYTHON" -m venv --clear "$VENV"
  force_deps=1
fi

if [ "$force_deps" -eq 1 ] || [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  echo ">> installing cortex[dev]"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e ".[dev]"
  touch "$STAMP"
fi

# Map fleet smoke to a small unit subset (no live tests).
args=()
smoke=0
while [ $# -gt 0 ]; do
  case "$1" in
    -m)
      shift
      if [ "${1:-}" = "smoke" ]; then
        smoke=1
        shift
      else
        args+=("-m" "${1:-}")
        shift || true
      fi
      ;;
    -q|--tb|--tb=*|--tb=line)
      # keep quiet flags for pytest
      case "$1" in
        --tb) args+=("$1"); shift; args+=("${1:-}"); shift || true ;;
        *) args+=("$1"); shift ;;
      esac
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

export PYTHONPATH="${PWD}${PYTHONPATH:+:$PYTHONPATH}"

# eventbus-kit path differs by environment:
#   host / make:     /srv/docker/eventbus/eventbus-kit (or sibling of cortex)
#   ops-worker:      /eventbus-kit (compose mount; /srv/docker/eventbus is not mounted)
_eventbus_kit=""
for _cand in \
  "/eventbus-kit" \
  "/srv/docker/eventbus/eventbus-kit" \
  "$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/eventbus/eventbus-kit"
do
  if [ -f "${_cand}/eventbus/__init__.py" ]; then
    _eventbus_kit="$_cand"
    break
  fi
done
if [ -n "$_eventbus_kit" ]; then
  export PYTHONPATH="${_eventbus_kit}:${PYTHONPATH}"
fi

# address-kit (KBTX venue resolution). Same lookup pattern as eventbus-kit.
_address_kit=""
for _cand in \
  "/address-kit" \
  "/srv/docker/websites/address-kit" \
  "$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)/websites/address-kit"
do
  if [ -f "${_cand}/address_kit/__init__.py" ]; then
    _address_kit="$_cand"
    break
  fi
done
if [ -n "$_address_kit" ]; then
  export PYTHONPATH="${_address_kit}:${PYTHONPATH}"
fi

if [ "$smoke" -eq 1 ]; then
  # Fast hermetic subset (no eventbus/docker). Live suites excluded.
  echo ">> pytest smoke (unit subset)"
  exec "$VENV/bin/python" -m pytest -q --tb=line \
    tests/test_config_schema.py \
    tests/test_logging_utils.py \
    tests/test_skip_tokens.py \
    tests/test_bible_plan.py \
    "${args[@]+"${args[@]}"}"
fi

echo ">> pytest (hermetic; live tests skipped by default)"
exec "$VENV/bin/python" -m pytest -q --tb=line \
  --ignore=tests/career_live \
  --ignore=tests/assorted_live \
  --ignore=tests/manual_live_runs \
  "${args[@]+"${args[@]}"}"
