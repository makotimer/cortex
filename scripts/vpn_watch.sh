#!/usr/bin/env bash
# Sample the VPN container from the host while a survey runs.
#
# Why this is separate from vpn_survey.py
# ---------------------------------------
# The survey runs *inside* a container and can only see what gluetun chooses to
# report on :8000. That blinded the 2026-08-13 run: an autoheal container from
# an unrelated stack restarted cortex-vpn-1 eight times mid-rotation, and the
# only way to find it afterwards was hand-correlating `docker logs` against
# record timestamps. Every switch over 90 s turned out to be one of these, which
# had been read as slow Proton exits.
#
# So sample from the host, where the restart is visible, and write it beside the
# survey's own output for correlation:
#
#   started_at   changes the instant the container is restarted by anything
#   health       what Docker thinks, which is what invites autoheal in
#   tun0 rx/tx   whether the tunnel is actually moving bytes, independent of
#                anything gluetun claims about itself. This is the
#                WireGuard-level evidence `wg show` would give — the gluetun
#                image ships no `wg` binary, but the interface counters are
#                right there in /proc/net/dev.
#
# A sample where exec fails is itself the datum: the container is mid-restart.
set -uo pipefail

CORTEX_DIR=/srv/docker/cortex
CONTAINER="${CONTAINER:-cortex-vpn-1}"
INTERVAL="${INTERVAL:-2}"
OUT_DIR="$CORTEX_DIR/local/state/vpn_survey"
STOP_AT="${STOP_AT:-}"

cd "$CORTEX_DIR"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/watch-$(date +%Y%m%dT%H%M%S).jsonl"

deadline=""
if [ -n "$STOP_AT" ]; then
    deadline=$(date -d "today $STOP_AT" +%s)
    # A stop time already past means tomorrow, matching vpn_survey.py.
    [ "$deadline" -le "$(date +%s)" ] && deadline=$(date -d "tomorrow $STOP_AT" +%s)
fi

# Gluetun's own account of a failed switch, which the survey cannot get.
#
# A switch that never produces an IP is recorded as `came_up: false` and nothing
# else — no country, no server, no reason, because there is no exit to ask about.
# That was ~4.5% of the first 457 records and is now the largest unexplained
# thing in the data. The control server refuses connections during exactly this
# window, so the answer is only reachable from the host, here.
#
# Dumped on every health transition rather than continuously: the log is only
# interesting around a state change, and a tail per sample would be 30x the
# volume of the samples themselves.
LOGS="$OUT_DIR/gluetun-$(date +%Y%m%dT%H%M%S).log"
prev_health=""

echo "watching $CONTAINER every ${INTERVAL}s -> $OUT"
echo "gluetun log tails on health transitions -> $LOGS"
[ -n "$deadline" ] && echo "stopping at $STOP_AT"

while true; do
    [ -n "$deadline" ] && [ "$(date +%s)" -ge "$deadline" ] && break

    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    started=$(docker inspect "$CONTAINER" --format '{{.State.StartedAt}}' 2>/dev/null || echo "")
    health=$(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo "")

    if [ "$health" != "$prev_health" ]; then
        {
            echo "===== $ts  health: ${prev_health:-none} -> ${health:-unknown}"
            docker logs --tail 40 --timestamps "$CONTAINER" 2>&1 || echo "(logs unavailable)"
        } >> "$LOGS"
        prev_health="$health"
    fi

    # One exec for both counters; a failure here means mid-restart, not an error.
    dev=$(docker exec "$CONTAINER" cat /proc/net/dev 2>/dev/null | awk '/tun0:/ {print $2, $10}')
    if [ -n "$dev" ]; then
        rx=${dev%% *}; tx=${dev##* }
    else
        rx=null; tx=null
    fi

    printf '{"ts":"%s","started_at":"%s","health":"%s","tun0_rx":%s,"tun0_tx":%s}\n' \
        "$ts" "$started" "$health" "${rx:-null}" "${tx:-null}" >> "$OUT"

    sleep "$INTERVAL"
done

echo "watch finished: $(wc -l < "$OUT") samples in $OUT"
