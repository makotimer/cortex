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
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/app")

import requests

from modules._shared import vpn_client

DEFAULT_OUT = "/app/local/state/vpn_survey"

#: Probed on every exit. All three are purpose-built connectivity-check
#: endpoints — they exist to be hit by arbitrary clients from arbitrary IPs, so
#: walking a hundred exits past them is exactly the traffic they expect.
#:
#: The first version of this used the real targets (Tockify's ICS feed, Lever's
#: posting API) on every exit. That is ~130 requests arriving from ~130 distinct
#: IPs within two hours, which is a fine way to get cortex's actual scraping
#: targets to blacklist the address pool it depends on. Not worth it for a
#: diagnostic.
DEFAULT_TARGETS = [
    ("control", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("gstatic-204", "http://connectivitycheck.gstatic.com/generate_204"),
    ("firefox-txt", "https://detectportal.firefox.com/success.txt"),
]

#: Hosts cortex genuinely scrapes. Sampled rather than probed every time: an
#: exit that reaches the neutral targets but not these is *blocked*, not broken,
#: and that distinction is worth keeping — just not at full volume.
REAL_TARGETS = [
    ("tockify-ics", "https://tockify.com/api/feeds/ics/bcslibrary"),
    ("lever", "https://api.lever.co/v0/postings/palantir?mode=json&limit=1"),
]

#: career_watch's schedule. Overlapping would restart the tunnel mid-scrape.
#:
#: Minutes matter here. This was hour-granular (5..19), which made the window run
#: to 18:59 — but career_watch's last scrape *starts* at 18:30 and is done within
#: a few minutes, and the cron wrapper is deliberately set to 18:45 to use that
#: gap. The mismatch meant the 2026-08-12 overnight run refused to start and
#: collected nothing at all.
BUSY_DAYS = {0, 1, 2, 3, 4, 5}          # Mon-Sat
BUSY_START = (5, 0)                     # 05:00, career_watch's first scrape
BUSY_END = (18, 40)                     # last scrape starts 18:30, done by ~18:35


def in_busy_window(now: datetime) -> bool:
    return (now.weekday() in BUSY_DAYS
            and BUSY_START <= (now.hour, now.minute) < BUSY_END)


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


def time_to_traffic(proxy: str, url: str, timeout: float,
                    offsets: list[float]) -> dict:
    """How long after gluetun reports an IP the tunnel actually carries traffic.

    Probing once and calling a failure a bad exit conflates two things: an exit
    that does not work, and an exit that is not ready yet. Walking a ladder of
    delays and recording the first success separates them, and the resulting
    distribution is what should set ``VERIFY_SETTLE_SECONDS`` — currently a
    round 6.0 chosen by judgement.
    """
    started = time.monotonic()
    attempts = []
    waited = 0.0
    for offset in offsets:
        if offset > waited:
            time.sleep(offset - waited)
        result = probe(proxy, url, timeout)
        elapsed = round(time.monotonic() - started, 2)
        attempts.append({"offset": offset, "elapsed": elapsed, "ok": result["ok"],
                         "error": result.get("error"), "status": result.get("status")})
        if result["ok"]:
            return {"ready": True, "seconds": elapsed, "offset": offset,
                    "attempts": attempts}
        # A probe that times out has already consumed wall-clock; measure the
        # next rung from the start, not from now, or a slow failure would
        # silently skip rungs.
        waited = time.monotonic() - started
    return {"ready": False, "seconds": None, "offset": None, "attempts": attempts}


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
                    help="how many exits to walk (default 25). For an overnight "
                         "run set this high and rely on --stop-at")
    ap.add_argument("--stop-at", default="04:30", metavar="HH:MM",
                    help="local wall-clock time to stop by (default 04:30, "
                         "which leaves career_watch's 05:00 run 30 minutes of "
                         "clear air). Checked before every switch, so a long "
                         "run cannot drift into a scrape")
    ap.add_argument("--pause", type=float, default=0.0,
                    help="seconds to idle between switches. Hundreds of "
                         "reconnects an hour is unusual traffic for a VPN "
                         "account; a pause makes a long run look less like a "
                         "hammer")
    ap.add_argument("--ladder", default="0,2,4,8,15", metavar="SECS",
                    help="comma-separated delays, measured from the moment "
                         "gluetun reports an IP, at which to probe the control "
                         "target until one succeeds (default 0,2,4,8,15). This "
                         "is what turns 'the settle retry rescued it' into a "
                         "time-to-traffic distribution. Empty string disables")
    ap.add_argument("--real-every", type=int, default=10, metavar="N",
                    help="additionally probe the real scraping targets (Tockify, "
                         "Lever) on every Nth exit, to keep detecting exits those "
                         "services block without hammering them from every IP in "
                         "the pool. 0 disables them entirely (default 10)")
    ap.add_argument("--recycle", choices=("off", "prev", "any"), default="prev",
                    help="switch again when the new exit is one we already have: "
                         "'prev' matches only the exit surveyed immediately "
                         "before (default), 'any' matches any exit seen this "
                         "run and maximises distinct coverage, 'off' keeps "
                         "every switch. Discarded switches are still recorded "
                         "under 'recycled' — their latency is a datum too")
    ap.add_argument("--recycle-max", type=int, default=3, metavar="N",
                    help="give up recycling after N extra switches, so a small "
                         "pool cannot spin the run in place (default 3)")
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

    deadline = parse_stop_at(args.stop_at, now)
    out_dir = Path(args.out_dir)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    out_path = out_dir / f"survey-{stamp}.jsonl"

    hours = (deadline - now).total_seconds() / 3600
    real = (f", real targets every {args.real_every}"
            if args.real_every else ", real targets disabled")
    print(f"switches={args.switches} targets={[n for n, _ in DEFAULT_TARGETS]}{real}")
    print(f"stopping by {deadline:%a %H:%M %Z} ({hours:.1f}h from now)")
    print(f"writing  {out_path}")
    if args.dry_run:
        print("dry run: nothing touched")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    client = vpn_client.GluetunClient(control_url=args.control_url,
                                      rotate_timeout=args.switch_timeout)
    ladder = [float(s) for s in args.ladder.split(",") if s.strip()]
    records: list[dict] = []
    prev_ip: str | None = None
    seen: set[str] = set()

    stopped_early = ""
    with out_path.open("w", encoding="utf-8") as fh:
        for i in range(1, args.switches + 1):
            # Checked every iteration, not just at startup: an overnight run
            # must stop on its own before the next scrape, whatever --switches
            # says and however long each switch turned out to take.
            check = datetime.now(UTC).astimezone()
            if check >= deadline:
                stopped_early = f"reached stop time {deadline:%H:%M}"
                break
            if in_busy_window(check) and not args.force:
                stopped_early = "career_watch window opened"
                break
            if args.pause and i > 1:
                time.sleep(args.pause)

            # Switching does not guarantee moving: the pool re-serves servers,
            # and two consecutive switches have landed on the same exit. Spending
            # a slot re-probing an exit just measured buys nothing, so switch
            # again — but keep the discarded switch, because its latency is a
            # datum about rotation even when its exit is a repeat.
            recycled: list[dict] = []
            while True:
                t0 = time.monotonic()
                ip = client._restart_and_wait()
                switch_seconds = round(time.monotonic() - t0, 2)
                if not ip or args.recycle == "off":
                    break
                repeat = (ip == prev_ip) if args.recycle == "prev" else (ip in seen)
                if not repeat or len(recycled) >= args.recycle_max:
                    break
                recycled.append({"ip": ip, "switch_seconds": switch_seconds,
                                 "matched": "prev" if ip == prev_ip else "seen"})

            identity = exit_identity(args.control_url, 5.0) if ip else {}
            rec = {
                "n": i,
                "ts": datetime.now(UTC).isoformat(),
                "switch_seconds": switch_seconds,
                "switch_timeout": args.switch_timeout,
                "came_up": bool(ip),
                "ip": ip,
                # Switches discarded for landing on an exit we already had.
                # Empty on the overwhelming majority of slots.
                "recycled": recycled,
                # True when recycling ran out of attempts and we surveyed a
                # repeat anyway — otherwise a repeat looks like a fresh exit.
                "is_repeat": bool(ip) and (ip == prev_ip or ip in seen),
                "country": identity.get("country"),
                "region": identity.get("region"),
                "city": identity.get("city"),
                "hostname": identity.get("hostname"),
                "organization": identity.get("organization"),
                # Gluetun's control API refuses connections outright while it
                # restarts WireGuard, so a health check that lands in that gap
                # reads as a failure. Record it rather than losing it to stderr.
                "identity_error": identity.get("error"),
                "targets": {},
            }
            if ip and ladder:
                control_url = dict(DEFAULT_TARGETS)["control"]
                rec["time_to_traffic"] = time_to_traffic(
                    args.proxy_url, control_url, args.probe_timeout, ladder)
            targets = list(DEFAULT_TARGETS)
            if args.real_every and i % args.real_every == 0:
                targets += REAL_TARGETS
            if ip:
                for name, url in targets:
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
            prev_ip = ip
            if ip:
                seen.add(ip)

            flags = "".join("." if t["ok"] else "X" for t in rec["targets"].values())
            ttt = rec.get("time_to_traffic") or {}
            ready = f"+{ttt['seconds']:.1f}s" if ttt.get("ready") else (
                "never" if ttt else "")
            recy = f" recycled x{len(recycled)}" if recycled else ""
            repeat = " REPEAT" if rec["is_repeat"] else ""
            print(f"  {i:3}/{args.switches}  {switch_seconds:6.1f}s  "
                  f"{(ip or 'NO IP'):16} {(rec['country'] or '?')[:14]:14} "
                  f"[{flags}] {ready}{recy}{repeat}")

    summarize(records)
    if stopped_early:
        print(f"\nstopped early: {stopped_early}")
    print(f"wrote {len(records)} records to {out_path}")
    return 0


def parse_stop_at(value: str, now: datetime) -> datetime:
    """HH:MM local -> the next such moment. Tomorrow if it already passed."""
    try:
        hh, mm = (int(x) for x in value.split(":", 1))
        stop = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (ValueError, TypeError):
        raise SystemExit(f"--stop-at: expected HH:MM, got {value!r}") from None
    if stop <= now:
        stop += timedelta(days=1)
    return stop


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
