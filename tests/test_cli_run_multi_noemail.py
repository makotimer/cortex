def test_cli_run_respects_no_email(stub_emailer, capsys):
    from service import cli

    rc = cli.main(["run", "modules.example_daily", "--kwargs", 'name="Ben"', "--no-email", "--print-html"])
    assert rc == 0
    # Stub should not have captured any messages
    assert stub_emailer.sent["messages"] == []
    # CLI printed SUCCESS and HTML block
    out, _ = capsys.readouterr()
    assert "SUCCESS" in out or "DONE" in out
