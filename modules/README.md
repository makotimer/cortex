# Module Author’s Guide  
**How to build a perfect scheduler module** — 30 seconds to read, 5 minutes to code.

```text
modules/
   my_module/
      __init__.py   ← from .main import run
      main.py       ← def run(**kwargs) -> str | tuple[str, dict] | None
      lib/
         utils.py
         engine.py
         render.py
```

## Rules (never break)

1. Folder name = module name
```modules.my_module → config.json: "module": "modules.my_module"```
2. `__init__.py` (3 lines)
```# modules/my_module/__init__.py
from .main import run  # noqa: F401
__all__ = ["run"]
```
3. `main.py` → `run(**kwargs)`
```py
def run(**kwargs: Any) -> str | tuple[str, dict] | None:
    """
    Return:
      - None → no email
      - "<html>" → email body
      - ("<html>", {"subject": "…"}) → body + meta
    """
```
4. All helpers → `lib/` - keep `main.py` tiny.
5. `*_env` magic (auto-list!)
```json
{
  "id": "bible-plan-mon-thu-0455",
  "module": "modules.bible_plan",
  "trigger": {
    "daily_time": {
      "time": "04:55",
      "day_of_week": "mon-thu"
    }
  },
  "email_to_env": "BIBLE_PLAN_EMAILS",
  "send_email": true,
  "kwargs": {
    "person_env": "BIBLE_PLAN_PERSON"
  },
  "summary": "Bible plan (Mon-Thu @ 04:55)"
}
```
```
BIBLE_PLAN_EMAILS=you@example.com,friend@example.com
BIBLE_PLAN_PERSON=Ben
```
`kwargs` variables ending in *_env will replaced by their contents from the environment.  Only `email_to_env`, `email_cc_env`, `email_bcc_env` from top level JSON will be replaced.

**Notes**: 
 - `send_email` defaults to true
 - `person_env` not actually used in `bible_plan`