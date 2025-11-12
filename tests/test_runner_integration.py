import re


def test_runner_calls_module_and_returns_html(stub_emailer, frozen_utc):
    from service import runner

    html, run_id = runner.run_module_once(
        module="modules.example_daily",
        kwargs={"name": "Ben", "items": ["alpha", "beta"]},
        send_email=False,  # runner should honor and not call emailer
    )
    assert isinstance(run_id, str) and re.match(r"^[a-f0-9-]+$", run_id)
    assert "<html" in html.lower()


def test_runner_email_path_when_enabled(stub_emailer, frozen_utc, monkeypatch):
    from service import runner

    monkeypatch.setenv("SEND_EMAIL", "1")
    monkeypatch.delenv("SCHEDULED_MODULES_DRY_RUN", raising=False)

    # Force send_email=True and provide a destination
    _, _ = runner.run_module_once(
        module="modules.example_daily",
        kwargs={"name": "Ben"},
        send_email=True,
        email_to=["test@example.org"],
        subject="[test] subject override",
    )
    # our stub captured the call
    sent = stub_emailer.sent.get("messages", [])
    assert len(sent) == 1
    assert sent[0]["subject"].startswith("[test] subject override")
    assert "html" in sent[0]
