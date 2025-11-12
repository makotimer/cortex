# service/cli.py
"""
User-facing command-line entrypoints for the container.

Subcommands
-----------
serve
    - Starts the APScheduler service loop via service.scheduler.start()
    - Starts the IMAP listener in a secondary thread via service.imap_listener.start()
    - Registers signal handlers for graceful shutdown of both components

run MODULE [--kwargs k=v ...] [--no-email] [--print-html]
    - Executes a module ad-hoc via runner.run_module_once(...)
    - Displays a concise success/failure summary
    - Optionally prints HTML returned by the run (best-effort detection)

list-jobs
    - Loads config via config_schema.load_config() and prints configured jobs

validate-config
    - Loads/validates config and returns nonzero on error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from service import imap_listener as _imap
from service import logging_utils as L
from service import runner as _runner
from service import scheduler as _scheduler

try:
    from service import config_schema as _config_schema
except Exception as e:  # pragma: no cover
    _config_schema = None  # type: ignore
    _CONF_IMPORT_ERR = e
else:
    _CONF_IMPORT_ERR = None


LOG = logging.getLogger("service.cli")


# ----------------------------- Logging setup ---------------------------------
def _ensure_logging() -> None:
    """Initialize a reasonable logging setup if none exists yet."""
    root = logging.getLogger()
    if not root.handlers:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        logging.basicConfig(
            level=getattr(logging, level, logging.INFO),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )


# -------------------------- Utility / glue code ------------------------------
def _parse_kv_pairs(pairs: Iterable[str]) -> dict[str, Any]:
    """
    Parse key=value strings into a dict.
    - Values that look like JSON (true/false/null/number/object/array) are parsed.
    - Otherwise keep as raw strings.
    """
    out: dict[str, Any] = {}
    for raw in pairs:
        if "=" not in raw:
            raise argparse.ArgumentTypeError(f"--kwargs item must be key=value (got {raw!r})")
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise argparse.ArgumentTypeError(f"Invalid key in --kwargs item {raw!r}")
        try:
            # Try to JSON-decode the value (for numbers, bools, arrays, objects)
            out[k] = json.loads(v)
        except Exception:
            out[k] = v
    return out


@contextmanager
def _env_overrides(env: dict[str, str]):
    """Temporarily set environment variables."""
    old = {}
    try:
        for k, v in env.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _print_table(rows: Iterable[tuple[str, str]], headers: tuple[str, str] = ("ID", "DETAILS")) -> None:
    """Very simple two-column table printer."""
    rows = list(rows)
    w0 = max(len(headers[0]), *(len(r[0]) for r in rows)) if rows else len(headers[0])
    w1 = max(len(headers[1]), *(len(r[1]) for r in rows)) if rows else len(headers[1])
    sep = f"+-{'-' * w0}-+-{'-' * w1}-+"
    print(sep)
    print(f"| {headers[0].ljust(w0)} | {headers[1].ljust(w1)} |")
    print(sep)
    for c0, c1 in rows:
        print(f"| {c0.ljust(w0)} | {c1.ljust(w1)} |")
    print(sep)


def _extract_jobs_from_config(cfg: Any) -> Iterable[tuple[str, str]]:
    """
    Heuristically extract jobs for list-jobs.
    Supports:
      - cfg["jobs"]  (list of dicts with id/name + summary/cron/etc.)
      - cfg.jobs     (same, as attributes)
    """
    jobs = cfg.get("jobs") if isinstance(cfg, dict) else getattr(cfg, "jobs", None)

    if not jobs:
        return []

    out = []
    for idx, j in enumerate(jobs):
        if isinstance(j, dict):
            jid = str(j.get("id") or j.get("name") or idx)
            desc = j.get("summary") or j.get("description") or j.get("cron") or json.dumps(j, default=str)
        else:
            # Try attributes
            jid = str(getattr(j, "id", None) or getattr(j, "name", None) or idx)
            desc = (
                getattr(j, "summary", None)
                or getattr(j, "description", None)
                or getattr(j, "cron", None)
                or repr(j)
            )
        out.append((jid, str(desc)))
    return out


# ------------------------------ Subcommands ----------------------------------
def cmd_validate_config(args: argparse.Namespace) -> int:
    if _config_schema is None:
        LOG.error("config_schema is not available: %s", _CONF_IMPORT_ERR)
        return 2
    try:
        cfg = _load_config_with_optional_path(args.config)
        # Optional: call a validate() if provided
        validate = getattr(_config_schema, "validate", None)
        if callable(validate):
            validate(cfg)
        print("OK: configuration is valid.")
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        LOG.exception("Configuration validation failed: %s", e)
        print(f"ERROR: configuration invalid: {e}", file=sys.stderr)
        return 1


def cmd_list_jobs(args: argparse.Namespace) -> int:
    if _config_schema is None:
        LOG.error("config_schema is not available: %s", _CONF_IMPORT_ERR)
        return 2
    try:
        cfg = _load_config_with_optional_path(args.config)
        rows = list(_extract_jobs_from_config(cfg))
        if not rows:
            print("No jobs found in config.")
            return 0
        _print_table(rows, headers=("JOB", "DETAILS"))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        LOG.exception("Failed to list jobs: %s", e)
        print(f"ERROR: failed to list jobs: {e}", file=sys.stderr)
        return 1


def _now_iso():
    return datetime.now().astimezone().isoformat()


def cmd_run(args: argparse.Namespace) -> int:
    run_id = uuid.uuid4().hex
    start_time = time.monotonic()

    kwargs = _parse_kv_pairs(args.kwargs or [])
    LOG.debug("Run module %s with kwargs=%s", args.module, kwargs)

    # Best-effort: many projects wire "no email" as a dry-run env or a kw flag
    env = {}
    if args.no_email:
        env["SCHEDULED_MODULES_DRY_RUN"] = "1"
        env["SEND_EMAIL"] = "0"

    try:
        with _env_overrides(env):
            html, run_id = _runner.run_module_once(
                module=args.module,
                kwargs=kwargs,
                send_email=not args.no_email,
            )
            # Calculate duration
            duration_s = time.monotonic() - start_time
            duration_ms = int(duration_s * 1000)
            L.write_activity_log({
                "ts": _now_iso(),
                "event": "cli_run",
                "run_id": run_id,
                "module": args.module,
                "trigger_type": "adhoc",
                "emailed": not args.no_email,
                "kwargs": kwargs,
                "duration_ms": duration_ms,
            })

        # Console output
        status_line = "DONE: Module run completed."
        if html:
            status_line = "SUCCESS: HTML returned."
            if args.print_html:
                print("\n----- HTML OUTPUT -----\n")
                print(html)
        print(status_line)
        return 0

    except KeyboardInterrupt:
        return 130
    except Exception as e:
        duration_s = time.monotonic() - start_time
        print(f"FAILURE: {e}", file=sys.stderr)
        L.write_error_log({
            "ts": _now_iso(),
            "where": "cli.run",
            "module": args.module,
            "kwargs": kwargs,
            "error": repr(e),
            "duration_ms": int(duration_s * 1000),
        })
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """
    Run the scheduler loop + IMAP listener in a daemon-like fashion until
    a termination signal is received. Both services are stopped cleanly.
    """
    L.write_activity_log({"ts": _now_iso(), "event": "serve_start"})

    stop_event = threading.Event()
    running = SimpleNamespace(sched=None, imap_thread=None)

    def _graceful_shutdown(signum=None, frame=None):
        LOG.info("Signal %s received; initiating shutdown...", signum)
        stop_event.set()
        _safe_stop("scheduler", getattr(running, "sched", None))
        _safe_stop("imap_listener", getattr(running, "imap_thread", None))

    # Register signals early
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _graceful_shutdown)

    try:
        # Start scheduler: prefer start(config_path=...) if available
        if args.config:
            try:
                running.sched = _scheduler.start(config_path=args.config)  # type: ignore
            except TypeError:
                running.sched = _scheduler.start()  # type: ignore
        else:
            running.sched = _scheduler.start()  # type: ignore
        LOG.info("Scheduler started: %r", running.sched)

        cfg = _load_config_with_optional_path(args.config)

        def cfg_getter():
            return cfg

        def scheduler_getter():
            return running.sched._scheduler  # Expose the scheduler instance

        # Start IMAP listener with cfg_getter
        imap_handle = _imap.start(cfg_getter=cfg_getter, scheduler_getter=scheduler_getter)  # type: ignore
        running.imap_thread = imap_handle  # controller with .stop()/.join()
        LOG.info("IMAP listener started: %r", running.imap_thread)

        # Main wait loop (respond quickly to signals)
        while not stop_event.is_set():
            time.sleep(0.3)

        # On normal stop path, ensure components are stopped
        _safe_stop("scheduler", running.sched)
        _safe_stop("imap_listener", running.imap_thread)
        L.write_activity_log({"ts": _now_iso(), "event": "serve_stop"})
        return 0

    except KeyboardInterrupt:
        _graceful_shutdown("KeyboardInterrupt")
        return 130
    except Exception as e:
        LOG.exception("Fatal error in serve: %s", e)
        _graceful_shutdown("UnhandledException")
        return 1


def _safe_stop(name: str, handle: Any) -> None:
    """Best-effort stop & join semantics for either a thread or a scheduler-like object."""
    if handle is None:
        return
    try:
        # Try a stop() method (common for service controllers)
        stop = getattr(handle, "stop", None)
        if callable(stop):
            stop()
    except Exception:  # pragma: no cover
        LOG.exception("Error stopping %s", name)

    # Try join() if this is a thread or similar
    try:
        join = getattr(handle, "join", None)
        if callable(join):
            join(timeout=10.0)
    except Exception:  # pragma: no cover
        LOG.exception("Error joining %s", name)


def _load_config_with_optional_path(path: str | None) -> Any:
    """Load config via config_schema.load_config(), optionally with a specific path."""
    if _config_schema is None:
        raise RuntimeError(f"config_schema unavailable: {_CONF_IMPORT_ERR}")
    load_config = getattr(_config_schema, "load_config", None)
    if not callable(load_config):
        raise RuntimeError("config_schema.load_config() not found")

    if path:
        try:
            return load_config(path)
        except TypeError:
            # Older signature without path support
            os.environ["CONFIG_PATH"] = path
            return load_config()
    return load_config()


# ------------------------------- Argparse ------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m service.cli",
        description="Service command-line tools",
    )
    p.add_argument(
        "--config",
        help="Path to config file (fallbacks to CONFIG_PATH env or module default).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # serve
    sp = sub.add_parser("serve", help="Run the main scheduler loop and IMAP listener.")
    sp.set_defaults(func=cmd_serve)

    # run
    sp = sub.add_parser("run", help="Execute a module ad-hoc via runner.run_module_once().")
    sp.add_argument("module", help="Module name to run (e.g., bible_plan, jobwatch).")
    sp.add_argument(
        "--kwargs",
        metavar="k=v",
        nargs="*",
        help="Extra keyword arguments for the module (JSON values supported).",
    )
    sp.add_argument(
        "--no-email",
        action="store_true",
        help="Do everything except actually send emails (dry-run).",
    )
    sp.add_argument(
        "--print-html",
        action="store_true",
        help="If the module returns HTML, print it to stdout.",
    )
    sp.set_defaults(func=cmd_run)

    # list-jobs
    sp = sub.add_parser("list-jobs", help="Print all jobs from config.")
    sp.set_defaults(func=cmd_list_jobs)

    # validate-config
    sp = sub.add_parser("validate-config", help="Verify configuration correctness.")
    sp.set_defaults(func=cmd_validate_config)

    return p


# --------------------------------- Main --------------------------------------
def main(argv: Iterable[str] | None = None) -> int:
    _ensure_logging()
    parser = _build_parser()
    args = parser.parse_args(args=list(argv) if argv is not None else None)
    # Attach top-level args (like --config) to subcommand handlers
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
