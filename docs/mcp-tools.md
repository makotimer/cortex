# ProtonMail MCP tools

`service/mcp_server.py` is a FastMCP server that exposes the ProtonMail Bridge IMAP account as Claude Code tools. It is started on-demand by the `.mcp.json` wiring:

```bash
docker exec -i cortex-cortex-1 python -m service.mcp_server
```

If the tools aren't showing up, restart the Claude Code session — MCP servers connect at startup.

## Connection

The server connects to the Bridge using these env vars (from `.env`):

| Var | Default | Description |
|-----|---------|-------------|
| `PROTON_IMAP_HOST` | `cortex_bridge` | IMAP hostname |
| `PROTON_IMAP_PORT` | `143` | IMAP port |
| `BRIDGE_USERNAME` | — | Bridge login username |
| `BRIDGE_PASSWORD` | — | Bridge app password |

## Tool reference

### `list_folders()`

Lists all IMAP folders/labels with their flags. Call this first to discover folder names before using other tools.

**Returns:** Formatted text list of folder paths and flags.

---

### `list_emails(folder, limit?, unseen_only?)`

Lists emails in a folder, newest first, showing UID, date, sender, flags, and subject (truncated at 60 chars).

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `folder` | str | required | Folder name, e.g. `"INBOX"` or `"Labels/Newsletter"` |
| `limit` | int | `20` | Max emails to return |
| `unseen_only` | bool | `false` | Only return unread emails |

Folder names are resolved case-insensitively; short names like `"Command"` auto-expand to `"Labels/Command"`.

---

### `read_email(folder, uid)`

Reads the full content of one email: headers + body (truncated at 5000 chars). Prefers plain-text; falls back to tag-stripped HTML.

| Param | Type | Description |
|-------|------|-------------|
| `folder` | str | Folder containing the email |
| `uid` | int | UID from `list_emails` or `search_emails` |

---

### `search_emails(folder, subject_contains?, from_contains?, since_date?, limit?)`

Searches emails by subject text, sender address, or date. All filter params are optional and ANDed together.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `folder` | str | required | Folder to search |
| `subject_contains` | str | — | Text that must appear in the subject |
| `from_contains` | str | — | Text that must appear in the From address |
| `since_date` | str | — | IMAP date string, e.g. `"1-May-2026"` |
| `limit` | int | `20` | Max results, newest first |

---

### `move_email(folder, uid, destination)`

Moves a single email from one folder to another (copy + delete + expunge).

| Param | Type | Description |
|-------|------|-------------|
| `folder` | str | Source folder |
| `uid` | int | UID of the email to move |
| `destination` | str | Destination folder name |

---

### `move_emails(folder, uids, destination)`

Moves multiple emails in one operation.

| Param | Type | Description |
|-------|------|-------------|
| `folder` | str | Source folder |
| `uids` | list[int] | List of UIDs to move |
| `destination` | str | Destination folder name |

---

### `send_email(to, subject, body, cc?)`

Sends an email via ProtonMail Bridge SMTP. The body is rendered from markdown to HTML before sending.

| Param | Type | Description |
|-------|------|-------------|
| `to` | str | Recipient address(es), comma-separated |
| `subject` | str | Email subject |
| `body` | str | Email body in markdown |
| `cc` | str | Optional CC address(es), comma-separated |

> **Note:** `send_email` depends on the `markdown` package, which is currently installed in the container but is not declared in `pyproject.toml`. It will break on the next image rebuild until the dependency is added back.
