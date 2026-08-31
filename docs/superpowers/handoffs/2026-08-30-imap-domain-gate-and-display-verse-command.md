---
repo: /srv/docker/cortex
spec: docs/superpowers/specs/2026-08-30-imap-domain-gate-and-display-verse-design.md
plan: docs/superpowers/plans/2026-08-30-imap-domain-gate-and-display-verse.md
verify: ./scripts/pytest.sh tests/test_imap_auth.py tests/test_imap_approval.py tests/test_imap_scripture.py -q
service: cortex
timeout: 5400
---

# IMAP domain gate and Display Verse command

You are implementing the cortex half of emailed scripture for the family wall.
When you are done, Command-folder mail is gated on `COMMAND_ALLOWED_DOMAINS`
(`pricefam.email` only), and a first-body-line `Scripture: <ref>` publishes
`display.verse` to `events:display` with no reply. Display rendering is a later
ticket.

## Start here

1. Read `docs/superpowers/plans/2026-08-30-imap-domain-gate-and-display-verse.md`,
   the header and **Global Constraints**.
2. Read `docs/superpowers/specs/2026-08-30-imap-domain-gate-and-display-verse-design.md`
   once for why. The plan is what you implement.
3. Work **every task in order**. Each step is one action. TDD: test, fail,
   implement, pass, commit.

## The plan is the specification

Type the tests and implementation as written. Do not invent a Bible parser in
cortex. Do not send a reply for `SCRIPTURE`. Do not restore `approval_allowlist`.

## Constraints

- Tests: `./scripts/pytest.sh tests/<file>.py -q` (inside the cortex container).
  No live IMAP, no Proton, no display.
- Do not `git push`. Do not restart unrelated stacks.
- Set live `.env` `COMMAND_ALLOWED_DOMAINS=pricefam.email` only. Never commit
  `.env` or print secrets. Do not add `APPROVAL_ALLOWED_DOMAINS`.
- Proton sieve (body starts with `Scripture:` → Command) is documented, not
  automated.

## Done when

- [ ] The three IMAP test files in `verify` pass
- [ ] `approval_allowlist` is gone from config and from Python
- [ ] `SCRIPTURE` publishes `{ref}` to `events:display` and returns silence
- [ ] `COMMAND_ALLOWED_DOMAINS` is `pricefam.email` only; no second allowlist
