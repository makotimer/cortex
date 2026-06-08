# IMAP command system

Cortex watches a designated mailbox folder (default `Labels/Command`, set by `COMMANDS_FOLDER`) for incoming emails. The subject line is parsed as a command and a reply is sent back to the sender. If the subject doesn't contain a recognized keyword, the first matching line of the plain-text body is used instead.

## Sending a command

Send an email from an address whose domain is listed in `COMMAND_ALLOWED_DOMAINS` to your ProtonMail Bridge account. Put the command in the **subject line**.

The listener polls the folder and replies to the sender automatically.

## Commands

### `LIST`

Returns a formatted list of all scheduled jobs: ID, trigger, and next run time.

```
Subject: LIST
```

---

### `CAREER REPORT`

Runs `scripts/career_check.py` and replies with a plain-text summary of new job postings from the local SQLite database.

```
Subject: CAREER REPORT
```

---

### `RUN MODULE=<job-id>`

Executes a job immediately using the same kwargs and email recipients defined in `config.json`. Replies with a confirmation containing the run ID, or an error message.

```
Subject: RUN MODULE=bible-plan
```

**Optional flags** (append to subject, any order, case-insensitive):

| Flag | Values | Effect |
|------|--------|--------|
| `KWARGS={"key":"val"}` | JSON object | Merge/override the job's configured kwargs |
| `NO_EMAIL=true` | `true` / `false` | Run silently — no output email sent, no reply |
| `PRINT_HTML=true` | `true` / `false` | Reserved, currently unused |

```
Subject: RUN MODULE=bible-plan KWARGS={"for_date":"2026-06-01"}
Subject: RUN MODULE=career-watch NO_EMAIL=true
```

**Skip-next behavior** — if the job's next scheduled run is within 6 hours, that run is automatically cancelled and rescheduled 1 minute after the skipped fire time, preventing a double-send.

## Files

| File | Purpose |
|------|---------|
| `parser.py` | Parses a command string into a structured dict |
| `handlers.py` | Dispatches parsed commands; executes modules via `service.runner` |
| `templates.py` | HTML template for the `LIST` reply email |
