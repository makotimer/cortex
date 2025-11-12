from __future__ import annotations

import html


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _section(title: str, body_html: str) -> str:
    return f'<section style="margin:12px 0;"><h3 style="margin:0 0 6px 0;">{_esc(title)}</h3>{body_html}</section>'


def assemble_email_html(
    reading_link: str, calvin_url: str | None, mh_url: str | None, reflection_html: str | None
) -> str:
    head = """<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0f3d3e;">
                <tr>
                    <td align="center" style="padding:14px 10px;">
                    <span style="font-family:Arial,Helvetica,sans-serif;font-size:14px;letter-spacing:1px;color:#ffffff;text-transform:uppercase;">
                        • ⏸️ •  • 🙏 •
                    </span>
                    </td>
                </tr>
            </table>

            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background:#f8f5f0;">
            <tr>
                <td align="center" style="padding:30px 20px 40px;">
                <!-- Big, warm headline -->
                <h1 style="font-family:'Georgia', serif; font-size:28px; color:#5d4037; margin:0 0 12px;">
                    ✧ Pause & Pray ✧
                </h1>
                <!-- One-line invitation -->
                <p style="font-family:'Helvetica Neue',Arial,sans-serif; font-size:18px; color:#6d4c41; line-height:1.4; margin:0 0 20px; max-width:480px;">
                    “Open my eyes, that I may see wondrous things from Your law.” <em>(Ps. 119:18)</em>
                </p>
                <!-- Subtle verse footer -->
                <p style="font-family:'Georgia',serif; font-size:14px; color:#8d6e63; font-style:italic; margin:20px 0 0;">
                    “…that you may be filled with the knowledge of His will in all wisdom and spiritual understanding.” — Colossians 1:9
                </p>
                </td>
            </tr>
            </table>

            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0f3d3e;">
            <tr>
                <td align="center" style="padding:14px 10px;">
                <span style="font-family:Arial,Helvetica,sans-serif;font-size:14px;letter-spacing:1px;color:#ffffff;text-transform:uppercase;">
                    ➡️ Proceed ➡️
                </span>
                </td>
            </tr>
            </table>"""  # noqa: E501
    links_html = (
        "<ul>"
        + "".join([
            f"<li>📖 Scripture — {reading_link}</li>",
            "<li>📚 Calvin — "
            + (f'<a href="{_esc(calvin_url)}">{_esc(calvin_url)}</a>' if calvin_url else "<em>n/a</em>")
            + "</li>",
            "<li>📚 Matthew Henry — "
            + (f'<a href="{_esc(mh_url)}">{_esc(mh_url)}</a>' if mh_url else "<em>n/a</em>")
            + "</li>",
        ])
        + "</ul>"
    )
    body = head + _section("Focus", links_html) + (_section("Content", reflection_html) if reflection_html else "")
    return body
