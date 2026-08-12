#!/usr/bin/env python3
"""Cycle VPN exits and record how each one actually behaves.

Why this exists
---------------
Production logs carry 750 rotations across 220 distinct exit IPs and not one
recorded failure — because the exit IP was only ever logged when a switch
*succeeded*, and nothing ever linked an exit to whether the fetch after it
worked. So there is no evidence about which servers are good, and the
quarantine in ``modules/_shared/vpn_client.py`` can only learn from real runs,
which happen twice a week.

This walks a lot of exits on purpose and writes one JSONL record per exit:
identity, how long the switch took, and whether each target was reachable
through it. That is the raw material for choosing VPN_ROTATE_TIMEOUT,
VPN_SWITCH_ATTEMPTS and the quarantine TTL from data instead of judgement.

Run it inside the cortex container::

    docker compose run --rm cortex python scripts/vpn_survey.py --switches 25

It monopolises the tunnel: every switch restarts WireGuard, which would break
any scrape running at the same time. career_watch runs Mon-Sat 05:00-18:30
Central, so the script refuses to start inside that window unless --force.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "/app")

import requests

from modules._shared import vpn_client

DEFAULT_OUT = "/app/local/state/vpn_survey"

#: Verified per exit. Each is a host something in cortex actually fetches, plus
#: one neutral control: if the control passes and a real target does not, the
#: exit is blocked rather than broken, which is a different problem.
DEFAULT_TARGETS = [
    ("tockify-ics", "https://tockify.com/api/feeds/ics/bcslibrary"),
    ("lever", "https://api.lever.co/v0/postings/palantir?mode=json&limit=1"),
    ("control", "https://www.cloudflare.com/cdn-cgi/trace"),
]

#: career_watch's schedule. Overlapping would restart the tunnel mid-scrape.
BUSY_DAYS = {0, 1, 2, 3, 4, 5}          # Mon-Sat
BUSY_START, BUSY_END = 5, 19            # 05:00-18:59 Central


def in_busy_window(now: datetime) -> bool:
    return now.weekday() in BUSY_DAYS and BUSY_START <= now.hour < BUSY_END


def probe(proxy: str, url: str, timeout: float) -> dict:
    """One target through the proxy. Never raises — a failure is the datum."""
    started = time.monotonic()
    try:
        r = requests.get(url, proxies={"http": proxy, "https": proxy},
                         timeout=timeout, stream=True)
        # Read a little, so we measure a real transfer rather than just headers.
        body = next(r.iter_content(2048), b"")
        return {"ok": r.status_code < 400, "status": r.status_code,
                "bytes": len(body), "seconds": round(time.monotonic() - started, 2)}
    except Exception as exc:
        return {"ok": False, "status": None, "error": type(exc).__name__,
                "detail": str(exc)[:200],
                "seconds": round(time.monotonic() - started, 2)}


def exit_identity(control_url: str, timeout: float) -> dict:
    """Everything gluetun knows about the current exit, verbatim."""
    try:
        r = requests.get(f"{control_url}/v1/publicip/ip", timeout=timeout)
        r.raise_for_status()
        return dict(r.json())
    except Exception as exc:
        return {"public_ip": "", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--switches", type=int, default=25,
                    help="how many exits to walk (default 25)")
    ap.add_argument("--control-url", default="http://vpn:8000")
    ap.add_argument("--proxy-url", default="http://vpn:8888")
    ap.add_argument("--switch-timeout", type=float,
                    default=vpn_client.DEFAULT_ROTATE_TIMEOUT,
                    help="seconds to wait for the tunnel after a restart")
    ap.add_argument("--probe-timeout", type=float, default=15.0)
    ap.add_argument("--settle", type=float,
                    default=vpn_client.VERIFY_SETTLE_SECONDS,
                    help="pause before re-probing a target that failed once")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true",
                    help="run even during career_watch hours (it will collide)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without touching the tunnel")
    args = ap.parse_args()

    now = datetime.now(UTC).astimezone()
    if in_busy_window(now) and not args.force:
        print(f"Refusing to run at {now:%a %H:%M %Z}: career_watch runs Mon-Sat "
              f"05:00-18:30 and every switch restarts the tunnel. Use --force "
              f"to override, or run in the evening.", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"survey-{stamp}.jsonl"

    print(f"switches={args.switches} targets={[n for n, _ in DEFAULT_TARGETS]}")
    print(f"writing  {out_path}")
    if args.dry_run:
        print("dry run: nothing touched")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    client = vpn_client.GluetunClient(control_url=args.control_url,
                                      rotate_timeout=args.switch_timeout)
    records: list[dict] = []

    with out_path.open("w", encoding="utf-8") as fh:
        for i in range(1, args.switches + 1):
            t0 = time.monotonic()
            ip = client._restart_and_wait()
            switch_seconds = round(time.monotonic() - t0, 2)

            identity = exit_identity(args.control_url, 5.0) if ip else {}
            rec = {
                "n": i,
                "ts": datetime.now(UTC).isoformat(),
                "switch_seconds": switch_seconds,
                "switch_timeout": args.switch_timeout,
                "came_up": bool(ip),
                "ip": ip,
                "country": identity.get("country"),
                "region": identity.get("region"),
                "city": identity.get("city"),
                "hostname": identity.get("hostname"),
                "organization": identity.get("organization"),
                "targets": {},
            }
            if ip:
                for name, url in DEFAULT_TARGETS:
                    first = probe(args.proxy_url, url, args.probe_timeout)
                    entry = {**first, "settled": False}
                    if not first["ok"]:
                        # Same discipline as vpn_client: one failure may only
                        # mean the tunnel is still coming up. Recording both
                        # attempts is the point — how often the retry rescues a
                        # probe is what says whether the settle delay is right.
                        time.sleep(args.settle)
                        second = probe(args.proxy_url, url, args.probe_timeout)
                        entry = {**second, "settled": True, "first": first}
                    rec["targets"][name] = entry
            rec["all_ok"] = bool(ip) and all(t["ok"] for t in rec["targets"].values())
            rec["rescued_by_settle"] = [
                n for n, t in rec["targets"].items() if t.get("settled") and t["ok"]]

            fh.write(json.dumps(rec) + "\n")
            fh.flush()          # survive a kill mid-survey
            records.append(rec)

            flags = "".join("." if t["ok"] else "X" for t in rec["targets"].values())
            print(f"  {i:3}/{args.switches}  {switch_seconds:6.1f}s  "
                  f"{(ip or 'NO IP'):16} {(rec['country'] or '?')[:14]:14} [{flags}]")

    summarize(records)
    print(f"\nwrote {len(records)} records to {out_path}")
    return 0


def summarize(records: list[dict]) -> None:
    if not records:
        return
    up = [r for r in records if r["came_up"]]
    good = [r for r in up if r["all_ok"]]
    print("\n" + "=" * 62)
    print(f"came up            : {len(up)}/{len(records)}")
    print(f"fully usable       : {len(good)}/{len(records)}")

    times = [r["switch_seconds"] for r in up]
    if times:
        times.sort()
        pct = lambda p: times[min(len(times) - 1, int(len(times) * p))]  # noqa: E731
        print(f"switch seconds     : median {statistics.median(times):.1f}  "
              f"p90 {pct(0.90):.1f}  max {max(times):.1f}")
        print("  -> a timeout below p90 discards exits that would have worked")

    per_target: dict[str, Counter] = defaultdict(Counter)
    for r in up:
        for name, t in r["targets"].items():
            per_target[name][bool(t["ok"])] += 1
    print("\nreachability by target:")
    for name, c in per_target.items():
        total = c[True] + c[False]
        print(f"  {name:12} {c[True]:3}/{total:3} ok")

    rescued = sum(len(r.get("rescued_by_settle") or []) for r in up)
    probed_twice = sum(1 for r in up for t in r["targets"].values() if t.get("settled"))
    if probed_twice:
        print(f"\nfirst probe failed {probed_twice} time(s); the settle retry "
              f"rescued {rescued}")
        print("  -> rescued/probed_twice is how often an exit was merely slow, "
              "not bad.\n     High here means condemning on one probe would "
              "blacklist good servers.")

    by_country: dict[str, Counter] = defaultdict(Counter)
    for r in up:
        by_country[r["country"] or "?"][r["all_ok"]] += 1
    print("\nfully usable by country:")
    for country, c in sorted(by_country.items(), key=lambda kv: -sum(kv[1].values())):
        total = c[True] + c[False]
        print(f"  {country[:22]:22} {c[True]:3}/{total:3}")

    repeats = Counter(r["ip"] for r in up if r["ip"])
    dupes = {ip: n for ip, n in repeats.items() if n > 1}
    if dupes:
        print(f"\nexits seen more than once: {len(dupes)} "
              f"(the pool re-serves servers; switching twice may not move you)")


if __name__ == "__main__":
    raise SystemExit(main())
