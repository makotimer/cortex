# config.json schema reference

`local/config.json` (path set by `CONFIG_PATH` env var) drives the scheduler. JSON and YAML are both accepted.

## Top-level structure

```json
{
  "timezone": "America/Chicago",
  "jobs": [ ... ]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `jobs` | list | required | List of job objects |
| `timezone` | string | `$TZ` env or `UTC` | Default timezone for all jobs |

---

## Job fields

### Required

| Field | Type | Description |
|-------|------|-------------|
| `module` | string | Dotted import path, e.g. `"modules.bible_plan"` |
| `trigger` | object | Exactly one trigger key (see below) |

### Identity

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique job identifier. Derived from `name`, then `module`, if absent. Used in `RUN MODULE=<id>` commands. |
| `name` | string | Alias for `id` (lower precedence) |
| `summary` | string | Human-readable label shown in `LIST` replies and command confirmation emails |
| `description` | string | Free-text notes, not used at runtime |

### Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | If `false`, the job is loaded/validated but never scheduled by the APScheduler (skipped at startup with a log line). Ad-hoc `RUN` commands may still invoke it. Use to temporarily disable without deleting the entry. |

### Email

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `send_email` | bool | `true` | Whether to send the HTML returned by `run()` as an email |
| `email_to` | string \| list | — | Recipient address(es) |
| `email_to_env` | string | — | Env var name whose value is a comma-separated recipient list (resolved at load time; takes precedence over `email_to`) |
| `email_cc` | string \| list | — | CC address(es) |
| `email_cc_env` | string | — | Env var name for CC list |
| `email_bcc` | string \| list | — | BCC address(es) |
| `email_bcc_env` | string | — | Env var name for BCC list |
| `subject` | string | — | Email subject override (runner uses module output subject if absent) |

### Execution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `kwargs` | object | `{}` | Keyword args passed to `run(**kwargs)`. Any value whose key ends in `_env` is replaced with the corresponding env var's value at load time. |
| `timeout_sec` | int ≥ 0 | — | Kill the job after this many seconds (0 = no timeout) |
| `coalesce` | bool | — | APScheduler coalesce: merge missed runs into one |
| `max_instances` | int ≥ 1 | — | Max concurrent instances of this job |
| `misfire_grace_time` | int ≥ 0 | — | Seconds after scheduled time within which a misfired job will still run |

---

## Trigger types

Exactly one key must appear inside the `trigger` object.

### `cron`

Standard crontab string or APScheduler cron fields object.

```json
"trigger": { "cron": "0 8 * * mon-fri" }
```

### `interval`

Object of APScheduler time kwargs. At least one field required.

```json
"trigger": { "interval": { "minutes": 90 } }
"trigger": { "interval": { "hours": 2, "minutes": 30 } }
```

Valid keys: `weeks`, `days`, `hours`, `minutes`, `seconds`.

### `date`

ISO-8601 datetime string. Runs once, then the job is removed.

```json
"trigger": { "date": "2026-06-01T09:00:00" }
```

### `daily_time`

Runs at fixed clock time(s) each day. Simpler than cron for daily schedules.

```json
"trigger": { "daily_time": "08:00" }

"trigger": { "daily_time": { "time": ["05:00", "17:00"], "day_of_week": "mon-sat" } }
```

`daily_time` object fields:

| Field | Type | Description |
|-------|------|-------------|
| `time` | string \| list | One or more `"HH:MM"` times (24-hour) |
| `day_of_week` | string | APScheduler day range, e.g. `"mon-fri"`, `"mon,wed,fri"` (optional) |
| `timezone` | string | Per-job timezone override (optional) |

---

## `_env` suffix pattern

Any `kwargs` value whose key ends with `_env` is replaced at load time with the value of the named environment variable:

```json
"kwargs": { "person_env": "SCRAPER_USER_1" }
```

With `SCRAPER_USER_1=The Archivist` in `.env`, the module receives `person="The Archivist"` (key with `_env` suffix stripped).

The same expansion applies to top-level `email_to_env`, `email_cc_env`, `email_bcc_env` — those expect comma-separated address lists.

---

## Full example

```json
{
  "timezone": "America/Chicago",
  "jobs": [
    {
      "id": "bible-plan",
      "module": "modules.bible_plan",
      "summary": "Bible plan (Mon–Thu @ 04:55)",
      "trigger": {
        "daily_time": { "time": "04:55", "day_of_week": "mon-thu" }
      },
      "email_to_env": "BIBLE_PLAN_EMAILS",
      "send_email": true,
      "kwargs": {}
    },
    {
      "id": "career-watch",
      "module": "modules.career_watch",
      "summary": "Career Watch (Mon–Sat, every 90 min)",
      "trigger": {
        "daily_time": {
          "time": ["05:00", "06:30", "08:00", "09:30", "11:00"],
          "day_of_week": "mon-sat"
        }
      },
      "email_to_env": "CAREER_WATCH_EMAILS",
      "send_email": true,
      "kwargs": {
        "person_env": "SCRAPER_USER_1",
        "sqlite_path": "/app/local/state/careerwatch.db",
        "max_threads": 8
      }
    }
  ]
}
```
