import signal
import types

from service import cli


def test_serve_starts_and_stops_email_worker(monkeypatch, write_min_config):
    started = {"v": False}
    stopped = {"v": False}

    class FakeController:
        def stop(self):
            stopped["v"] = True

        def join(self, timeout=None):
            pass

    class FakeSched:
        _scheduler = object()

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

    # Replace the three long-running components with fakes.
    monkeypatch.setattr(cli._scheduler, "start", lambda *a, **k: FakeSched())
    monkeypatch.setattr(cli._imap, "start", lambda **k: FakeController())

    def fake_email_start(*a, **k):
        started["v"] = True
        return FakeController()

    monkeypatch.setattr(cli._emailbus, "start", fake_email_start)

    # End the serve wait-loop deterministically: the first sleep raises KeyboardInterrupt,
    # which cmd_serve handles by calling _graceful_shutdown() (stopping the worker) and returning 130.
    def fake_sleep(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)

    # cmd_serve registers SIGINT/SIGTERM handlers; save and restore so we don't leak them.
    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        rc = cli.cmd_serve(types.SimpleNamespace(config=str(write_min_config)))
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)

    assert rc == 130
    assert started["v"] is True, "email worker was not started in serve"
    assert stopped["v"] is True, "email worker was not stopped on shutdown"
