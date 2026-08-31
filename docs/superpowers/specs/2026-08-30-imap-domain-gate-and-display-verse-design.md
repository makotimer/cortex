# IMAP domain gate and Display Verse command

**Date:** 2026-08-30
**Status:** design approved
**Paired spec:** `/srv/docker/websites/coviecraft/display/docs/superpowers/specs/2026-08-30-display-verse-row-design.md`
**This repo implements:** cortex only (IMAP auth + `Scripture:` command + bus publish)

## 1. Problem

The family wall at `http://r2d2:8088/` has no way to show a chosen Bible verse.
The intended trigger is ordinary email: a parent sends a reference, the kiosk
shows that verse as a one-line middle row.

Inbound email already reaches cortex’s IMAP listener, which watches Proton
`Labels/Command`. hs/pbd approvals already hop Command → `events:<site>`.
Display is not on the bus and cortex has no verse command.

Two related defects in the existing IMAP gate:

- `COMMAND_ALLOWED_DOMAINS` is documented (`.env`, IMAP README) and **never
  read** by Python. `LIST` / `RUN` / `CAREER REPORT` run for any sender whose
  mail lands in Command.
- `APPROVE` / `DENY` instead check `approval_allowlist` in `local/config.json`
  (live: `webmaster@pricefam.email` only). That list is the hs/pbd admin
  identity, not a general command gate.

## 2. Goals

- Any IMAP command (`LIST`, `RUN`, `CAREER REPORT`, `APPROVE`, `DENY`,
  `SCRIPTURE`) is accepted only if the sender’s domain is in
  `COMMAND_ALLOWED_DOMAINS` (live: `pricefam.email` **only**). Work-mail
  domains do not belong in cortex config.
- Delete `approval_allowlist`. Exact-address allowlisting goes away. There is
  no second env list.
- A Command-folder mail whose **first non-blank body line** is
  `Scripture: <ref>` publishes `{ref}` to `events:display` and **sends no
  reply**.
- Subject is ignored for this command (Proton filters often rewrite it).
- Cortex does **not** parse the reference beyond a non-empty remainder and
  does **not** load NKJV.json. Display owns lookup.

### Non-goals

- Rendering the verse (display spec).
- Changing Proton sieves in code (operator step, §8).
- A reply email for verse set / reject / lookup failure.
- Teaching cortex about Bible books, aliases, or verse ranges.
- Changing `email.send` outbound.
- Pushing git remotes.

## 3. End-to-end (this repo’s slice)

```
Proton sieve (body starts with Scripture:) → Labels/Command
  → imap_listener UID
  → handle_command
       sender domain ∈ COMMAND_ALLOWED_DOMAINS
       first body line matches Scripture: <ref>
       publish events:display  type=display.verse  payload={ref}
       return (None, None, None)   # listener sends no mail
  → display consumer (other repo)
```

Mail that never reaches Command is invisible. That is unchanged.

## 4. Sender authentication

Replace `approval_allowlist` with the env var that already exists and is
unused: `COMMAND_ALLOWED_DOMAINS` (comma-separated, stripped, lowercased,
empty entries dropped).

| Env | Live value | Applies to |
|---|---|---|
| `COMMAND_ALLOWED_DOMAINS` | `pricefam.email` | every IMAP command, including `SCRIPTURE`, `LIST`, `RUN`, `CAREER REPORT`, `APPROVE`, `DENY` |

Fail closed: an empty list rejects everyone.

Compare the **domain** of the envelope From (already passed in as `from_addr`,
`user@host`). Ignore display name. Case-insensitive. A missing / unparseable
From is a reject.

Do **not** add `APPROVAL_ALLOWED_DOMAINS`. Approvals use the same list as every
other command. Widening from `webmaster@pricefam.email` to any address at
`pricefam.email` is deliberate.

**`SCRIPTURE` from a rejected domain:** no publish, **no reply**.

**Any other command from a rejected domain:** no side effects, “Not authorized”
reply (same copy as today’s failed approval).

Do not read `cfg["approval_allowlist"]`. Remove that key from
`local/config.json`. Keep schema permissive (unknown keys already allowed).

Strip any work-mail domain from live `.env` `COMMAND_ALLOWED_DOMAINS` as part
of this work. It must not remain in cortex config.

`COMMAND_ALLOWED_DOMAINS` stays an env var, not a config.json array — that is
where it already lives.

## 5. Command parse

Existing flow (body command-keyword line, else subject) stays for `LIST` /
`RUN` / `APPROVE` / `DENY` / `CAREER REPORT`.

**New, higher priority:** after `_strip_quoted_reply(text)`, look at the
**first non-blank line only**. If it matches (case-insensitive):

```
^scripture\s*:\s*(.+)$
```

then the command is `SCRIPTURE` with `ref` = the captured remainder, stripped.
Do not consult the subject. Do not scan later lines (quoted `APPROVE` below a
`Scripture:` line must not win; a `Scripture:` on line 2 after a greeting must
not match).

`Scripture:` with empty remainder → `SCRIPTURE` with `ref=""` → no publish, no
reply.

Parser addition in `parse_command_line` is optional; the handler may match the
first line before calling the parser. Either way, tests pin:

| Body first line | Subject | Result |
|---|---|---|
| `Scripture: John 3:16` | `LIST` | `SCRIPTURE` ref `John 3:16` |
| `scripture: Jn 3:16-18` | (anything) | `SCRIPTURE` |
| `Scripture:` | | no publish |
| `VERSE John 3:16` | | not this command |
| `APPROVE hs-x` | `Scripture: John 3:16` | `APPROVE` (first body line is APPROVE) |
| empty body | `Scripture: John 3:16` | not SCRIPTURE (subject ignored) |

`Re:` / `Fwd:` stripping on the **subject** remains for other commands. Verse
never uses the subject.

## 6. Publish

New helper next to `publish_decision` in `service/site_events.py`:

```python
DISPLAY_VERSE = "display.verse"

def publish_verse(ref: str) -> str:
    return get_bus().publish(
        events_stream("display"), DISPLAY_VERSE,
        payload={"ref": ref},
    )
```

Do not add a constant to `eventbus-kit` in this ticket (avoids a third repo).
The type string is `display.verse`.

On handler success: call `publish_verse(ref)` then `return None, None, None`.
On bus error: log, still no reply (do not crash the listener loop; the
listener already catches per-UID exceptions).

Idempotency: two identical mails publish two events. Display’s file write is
last-write-wins. That is acceptable.

## 7. Silence

`imap_listener` already skips SMTP when `content is None`. `RUN … NO_EMAIL=true`
is the precedent. `SCRIPTURE` always takes that path, including parse failure
and domain failure.

Do not send “queued”, “unknown ref”, or “not authorized” for this command.

## 8. Operator: Proton sieve

One-time Proton filter, same class as `APPROVE` / `DENY`:

- If the body starts with `Scripture:` (case-insensitive), move/label into
  `Command`.

Not automated. Document in `service/imap_commands/README.md` and
`CLAUDE.md` next to the existing Command-folder note.

## 9. Tests (cortex)

Hermetic; fakeredis for publish tests (existing `test_imap_approval.py`
pattern).

- Command domain allow / deny for `LIST` (deny = “Not authorized” reply, no
  scheduler call).
- `APPROVE` from any address at an allowed domain publishes; a different
  domain does not. Pin the widening from webmaster-only: `other@example.com`
  is allowed when `COMMAND_ALLOWED_DOMAINS=example.com`.
- `approval_allowlist` in cfg is ignored.
- Scripture first line publishes `{ref}`; subject `LIST` does not run LIST.
- Scripture empty ref / bad domain / subject-only: no publish, return
  `(None, None, None)`.
- Quoted original containing `Scripture:` below a real first line does not
  override.

Do not hit Proton, Redis on the network, or display.

## 10. Docs in this repo

- `service/imap_commands/README.md`: domain env is real; `Scripture:` command;
  no reply; sieve.
- `CLAUDE.md` Command-folder bullet: approvals are domain-gated, not
  `approval_allowlist`.
- `.env.example`: `COMMAND_ALLOWED_DOMAINS` example stays generic (no work
  domain). Do not add a second allowlist env.
- `local/config.json`: delete `approval_allowlist`.
- Live `.env`: `COMMAND_ALLOWED_DOMAINS=pricefam.email` only (never commit
  `.env`).

## 11. Out of scope here

Display consumer, NKJV lookup, `/data/verse.json`, glance layout, joining the
`eventbus` network. Those are the paired display spec and its plan. Cortex can
ship and be tested without display running: publish is asserted against
fakeredis.
