#!/usr/bin/env bash
# reload.sh — Pull, build, recreate containers with optional cortex_bridge
# Usage: ./scripts/reload.sh [--bridge] [--dry-run] [-f compose.yaml] [--help]

set -euo pipefail

# Resolve project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_COMPOSE="$PROJECT_ROOT/docker-compose.yaml"

# Default values
COMPOSE_FILE="$DEFAULT_COMPOSE"
DRY_RUN=false
BRIDGE_FLAG=false
SERVICES=(cortex)

# Help
show_help() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --bridge          Also reload cortex_bridge service
  -f, --file PATH   Use custom docker-compose file (default: $DEFAULT_COMPOSE)
  --dry-run         Show commands without executing
  --help            Show this help

Example:
  $(basename "$0") --bridge -f docker-compose.prod.yaml
EOF
  exit 0
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bridge)
      BRIDGE_FLAG=true
      shift
      ;;
    -f|--file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      show_help
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      show_help
      ;;
  esac
done

# Validate compose file
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: Compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi

# Add cortex_bridge if requested
if [[ "$BRIDGE_FLAG" == true ]]; then
  SERVICES=(cortex_bridge cortex)
fi

# Detect compose command
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DC=(docker compose -f "$COMPOSE_FILE")
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose -f "$COMPOSE_FILE")
else
  echo "ERROR: Neither 'docker compose' nor 'docker-compose' found." >&2
  exit 1
fi

# Optional dry-run wrapper
run() {
  if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY-RUN] $*"
  else
    echo "==> $*"
    "$@"
  fi
}

# Log file: now in local/logs/
LOG_DIR="$PROJECT_ROOT/local/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/reload.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

log "Reload started by $(whoami) from $SCRIPT_DIR"
log "Using compose file: $COMPOSE_FILE"
log "Services: ${SERVICES[*]}"
[[ "$DRY_RUN" == true ]] && log "DRY RUN MODE"

echo "Reloading services: ${SERVICES[*]}"
echo "Compose file: $COMPOSE_FILE"
[[ "$DRY_RUN" == true ]] && echo "DRY RUN MODE"
echo "Log: $LOG_FILE"

run "${DC[@]}" pull "${SERVICES[@]}" || true

run "${DC[@]}" build --pull "${SERVICES[@]}"

run "${DC[@]}" up -d --force-recreate --remove-orphans --build "${SERVICES[@]}"

run "${DC[@]}" ps

echo "Done. Log: $LOG_FILE"
log "Reload completed successfully"