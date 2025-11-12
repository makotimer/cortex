from __future__ import annotations

from . import utils
from .models import Posting


def build_tables(by_source: dict[str, list[Posting]]) -> str:
    """
    Build the exact HTML sections/tables requested, grouped by source.

    Each section:
      <h3>{source}</h3>
      <table>
        Title | Link
        ...
      </table>
    """
    sections: list[str] = []
    for source, items in sorted(by_source.items()):
        row_html: list[str] = []
        for p in items:
            title = p.title or "(no title)"
            url = p.url or ""
            # Escape only the URL pieces and the title — NOT the <a> wrapper
            link_html = f'<a href="{utils.esc(url)}">{utils.esc(url)}</a>'
            row_html.append(f"<tr><td>{utils.esc(title)}</td><td>{link_html}</td></tr>")
        table_html = (
            "<table border='1' cellspacing='0' cellpadding='6'>"
            "<tr><th>Title</th><th>Link</th></tr>" + "".join(row_html) + "</table>"
        )
        sections.append(f"<h3>{utils.esc(source)}</h3>\n{table_html}")
    return "\n".join(sections)


def wrap_document(content_html: str, *, heading: str | None = None, intro: str | None = None) -> str:
    """
    Optional convenience: wrap tables in a minimal document structure.
    (Engine can use this to add a summary line and heading.)
    """
    parts: list[str] = ["<div>"]
    if heading:
        parts.append(f"<h2>{utils.esc(heading)}</h2>")
    if intro:
        parts.append(f"<p>{utils.esc(intro)}</p>")
    parts.append(content_html)
    parts.append("</div>")
    return "\n".join(parts)
