#!/usr/bin/env bash
# Overnight VPN exit survey, for cron.
#
# Runs after career_watch's last scrape of the day and stops before the next
# thing that needs the tunnel. The windows it has to fit between:
#
#   career_watch     Mon-Sat 05:00-18:30   (last run finishes ~18:35)
#   event_watch      Wed+Sun 03:40
#
# So: start 18:45, stop 03:20. Stopping at 04:30 would be fine most nights and
# would restart the tunnel under event_watch on Tuesday and Saturday nights.
#
# Self-limiting: this is a diagnostic, not a permanent job. Once TARGET_RECORDS
# exits have been surveyed it does nothing, so forgetting to remove the crontab
# line costs nothing rather than churning a VPN account nightly forever.
set -euo pipefail

CORTEX_DIR=/srv/docker/cortex
OUT_DIR="$CORTEX_DIR/local/state/vpn_survey"
TARGET_RECORDS="${TARGET_RECORDS:-1500}"
STOP_AT="${STOP_AT:-03:20}"
PAUSE="${PAUSE:-10}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

cd "$CORTEX_DIR"

if ! docker compose ps --status running --format '{{.Service}}' | grep -qx vpn; then
    log "vpn container is not running; nothing to survey"
    exit 0
fi

collected=0
if compgen -G "$OUT_DIR/*.jsonl" > /dev/null; then
    collected=$(cat "$OUT_DIR"/*.jsonl | wc -l)
fi

if [ "$collected" -ge "$TARGET_RECORDS" ]; then
    log "already have $collected records (target $TARGET_RECORDS); skipping."
    log "Remove this crontab line, or raise TARGET_RECORDS to keep going."
    exit 0
fi

log "starting survey: have $collected records, target $TARGET_RECORDS, stop-at $STOP_AT"
docker compose run --rm --no-deps -T cortex \
    python scripts/vpn_survey.py \
    --switches 5000 \
    --stop-at "$STOP_AT" \
    --pause "$PAUSE"
log "survey finished"
