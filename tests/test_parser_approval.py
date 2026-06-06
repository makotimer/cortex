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
