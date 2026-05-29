# tests/test_imap_listener_state.py
"""Regression tests for the IMAP command listener's state-file location.

Guards against the bug where the listener wrote its state file under
``/app/state`` — owned by root and not writable by the container user — which
raised ``PermissionError`` on every connection and crash-looped the listener so
that no emailed commands were ever processed. The writable, bind-mounted state
dir is ``/app/local/state`` (where the scheduler heartbeat also writes).
"""

from pathlib import Path

from service import imap_listener


def test_default_state_dir_is_the_writable_bind_mount():
    # /app/local/state is the bind-mounted, app-writable dir; the bare top-level
    # "state" dir under /app is root-owned and not writable by the container user.
    assert str(imap_listener._STATE_DIR) == "/app/local/state"


def test_state_uid_path_lives_under_base_and_creates_it(tmp_path, monkeypatch):
    monkeypatch.delenv("COMMAND_STATE_FILE", raising=False)
    base = tmp_path / "state"
    p = imap_listener._state_uid_path("Labels/Command", base=base)

    assert p == base / "command_last_uid_Labels_Command.txt"
    assert base.is_dir()  # created as a side effect
    assert p.parent == base
    # path-unsafe characters in the mailbox name are sanitised
    assert "/" not in p.name


def test_command_state_file_override_wins(tmp_path, monkeypatch):
    override = tmp_path / "custom" / "uid.txt"
    monkeypatch.setenv("COMMAND_STATE_FILE", str(override))

    p = imap_listener._state_uid_path("anything", base=tmp_path / "ignored")

    assert p == override
    assert override.parent.is_dir()


def test_no_source_references_unwritable_app_state_dir():
    """No service/ or modules/ source should reference /app/state.

    The writable path is /app/local/state. A bare /app/state literal is the
    root-owned, non-writable path that caused the listener crash-loop and the
    silently-failing LLM markdown archival. This catches the whole class.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for sub in ("service", "modules"):
        for py in (root / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            # /app/local/state does not contain the substring /app/state,
            # so a hit here is always the bad, non-writable path.
            if "/app/state" in text:
                offenders.append(str(py.relative_to(root)))
    assert not offenders, f"Use /app/local/state, not /app/state, in: {offenders}"
