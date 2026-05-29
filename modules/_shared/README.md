# modules/_shared — shared utilities

Common helpers for module authors. Import from `modules._shared.utils`; the other files are empty stubs reserved for future use.

## utils.py

```python
from modules._shared import utils
```

### `esc(s: str) -> str`

HTML-escapes a string (including quotes). Use whenever inserting untrusted strings into HTML output.

```python
f'<td>{utils.esc(title)}</td>'
```

### `md_to_html(md: str) -> str`

Hand-rolled markdown-to-HTML converter — no external dependencies. Supports:

- Headings `#`–`#####`
- Paragraphs
- Unordered lists (`-`, `*`, `+`) and ordered lists, nested
- Blockquotes `>`
- Fenced code blocks (`` ``` ``) with optional language class
- Inline `` `code` ``, `**bold**`, `*italic*`, `~~strikethrough~~`
- `[link text](url)` and bare `https://` URLs
- `---` horizontal rules

```python
html = utils.md_to_html(llm_response)
```

### `OpenAIChat`

Thin dataclass wrapping `openai.chat.completions`. Model name and temperature are resolved from environment variables at call time, so they can be changed without code edits.

```python
llm = utils.OpenAIChat(model_env="OPENAI_MODEL_BIBLE", temp_env="OPENAI_TEMP_BIBLE")
result = llm.chat(system_msg="You are a Reformed theologian.", user_msg="Explain Genesis 1.")
```

| Field | Type | Description |
|-------|------|-------------|
| `model_env` | `str` | Name of the env var holding the model ID (e.g. `"OPENAI_MODEL_BIBLE"`) |
| `temp_env` | `str` | Name of the env var holding the temperature (0.0–2.0) |
| `api_key_env` | `str` | Name of the env var holding the API key (default: `"OPENAI_API_KEY"`) |

**Optional LLM archival** — if `LLM_MD_ENABLE=1`, each response is written to a `.md` file under `LLM_MD_DIR` (default `/app/local/state/llm`). `LLM_MD_MAX` caps the number of files kept (0 = unlimited).

## Stubs (empty — do not import)

`cache.py`, `dates.py`, `email_ctx.py`, `html.py`, `http.py` are all empty placeholder files. `runner.py` references `html.build_email` with `# type: ignore[attr-defined]` as a known forward reference — the active email template is `_html_email_fallback` in `service/runner.py`.
