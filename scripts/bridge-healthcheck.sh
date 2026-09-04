#!/bin/sh
# Proton Bridge liveness: prove a real IMAP LOGIN succeeds, not merely that the
# port is listening.
#
# Why this is not a bare TCP connect: Bridge keeps its IMAP listener open even
# when it has loaded ZERO users. On 2026-08-26 a Proton API outage made the
# startup `auth/v4/refresh` fail ("Failed to load connected user"), Bridge never
# retried, and every SMTP AUTH returned 454 while the old
# `socat - TCP:127.0.0.1:1143` check stayed green. autoheal saw a healthy
# container and never restarted it, so all outbound mail died silently for 42
# hours. A LOGIN is the smallest check that actually exercises the user list.
#
# Credentials come from the same Bridge-generated pair cortex uses
# (BRIDGE_USERNAME / BRIDGE_PASSWORD in cortex/.env), injected as
# HEALTH_IMAP_USER / HEALTH_IMAP_PASS. They are Bridge-local, not the Proton
# account password.

set -u

PORT="${HEALTH_IMAP_PORT:-1143}"
USER="${HEALTH_IMAP_USER:-}"
PASS="${HEALTH_IMAP_PASS:-}"

if [ -z "$USER" ] || [ -z "$PASS" ]; then
    echo "bridge-healthcheck: HEALTH_IMAP_USER/HEALTH_IMAP_PASS unset" >&2
    exit 1
fi

# Bridge passwords are base64-ish; they contain no '"' or '\', so a plain IMAP
# quoted-string is safe. Guard anyway rather than emit a malformed command.
case "$USER$PASS" in
    *'"'* | *'\'*)
        echo "bridge-healthcheck: credential contains a quote/backslash" >&2
        exit 1
        ;;
esac

out=$(printf 'a1 LOGIN "%s" "%s"\r\na2 LOGOUT\r\n' "$USER" "$PASS" \
      | socat -T5 - "TCP:127.0.0.1:${PORT},connect-timeout=3" 2>/dev/null)

# Never echo $out: a NO response quotes the attempted credentials back.
case "$out" in
    *"a1 OK"*) exit 0 ;;
esac

echo "bridge-healthcheck: IMAP LOGIN did not return OK (no users loaded?)" >&2
exit 1
