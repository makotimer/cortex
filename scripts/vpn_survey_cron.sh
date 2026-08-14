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

# survey-*.jsonl only. The watcher writes watch-*.jsonl into the same directory
# at one sample every 2 s, so counting *.jsonl reaches the target within a single
# night's watching and the self-limit fires having collected almost no exits.
collected=0
if compgen -G "$OUT_DIR/survey-*.jsonl" > /dev/null; then
    collected=$(cat "$OUT_DIR"/survey-*.jsonl | wc -l)
fi

if [ "$collected" -ge "$TARGET_RECORDS" ]; then
    log "already have $collected records (target $TARGET_RECORDS); skipping."
    log "Remove this crontab line, or raise TARGET_RECORDS to keep going."
    exit 0
fi

# --real-every 1 (was 10) as of 2026-08-14. The whole production risk is an exit
# that reaches the neutral targets but is *blocked* by a job board — which looks
# exactly like a quiet day, the original symptom this survey exists to explain.
# It was the only thing sampled at 1-in-10: 159 of the first 457 exits got a real
# fetch. Those 159 came back 158/159 clean on both Tockify and Lever, so the
# hammering the sampling guarded against is not materialising, and full coverage
# is also the only way to get more than 5 samples on the Tor-over-VPN exits.

# Restarts by anything else on the host corrupt switch latency, and from inside
# the container they are invisible. Sample from the host in parallel so records
# can be labelled rather than reconstructed afterwards.
STOP_AT="$STOP_AT" "$CORTEX_DIR/scripts/vpn_watch.sh" &
watch_pid=$!
trap 'kill "$watch_pid" 2>/dev/null || true' EXIT

log "starting survey: have $collected records, target $TARGET_RECORDS, stop-at $STOP_AT"
# scripts/ is mounted because the container otherwise runs the copy baked into
# the image, which silently ignores every host-side edit until a rebuild.
docker compose run --rm --no-deps -T \
    -v "$CORTEX_DIR/scripts:/app/scripts" \
    cortex python scripts/vpn_survey.py \
    --switches 5000 \
    --stop-at "$STOP_AT" \
    --pause "$PAUSE" \
    --switch-timeout 120 \
    --ladder 0,2,4,8,15,30 \
    --recycle any \
    --real-every 1 \
    --settle 6
log "survey finished"
