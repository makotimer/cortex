from service.imap_commands.parser import parse_command_line


def test_parse_approve():
    assert parse_command_line("APPROVE hs-abc123") == {"command": "APPROVE", "token": "hs-abc123"}


def test_parse_deny_case_insensitive():
    assert parse_command_line("deny hs-XYZ_9") == {"command": "DENY", "token": "hs-XYZ_9"}


def test_parse_approve_requires_token():
    # bare APPROVE with no token must NOT be treated as an APPROVE command
    out = parse_command_line("APPROVE")
    assert out.get("command") != "APPROVE"


def test_existing_list_still_parses():
    assert parse_command_line("LIST") == {"command": "LIST"}


def test_reply_prefix_is_stripped():
    # Mail clients prepend "Re:"/"Fwd:" to a reply subject; the command must still parse.
    assert parse_command_line("Re: APPROVE hs-abc123") == {"command": "APPROVE", "token": "hs-abc123"}
    assert parse_command_line("RE: re: DENY hs-xyz_9") == {"command": "DENY", "token": "hs-xyz_9"}
    assert parse_command_line("Fwd: LIST") == {"command": "LIST"}
