from __future__ import annotations

import html

_HEADER = """<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0f3d3e;">
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
                    "Open my eyes, that I may see wondrous things from Your law." <em>(Ps. 119:18)</em>
                </p>
                <!-- Subtle verse footer -->
                <p style="font-family:'Georgia',serif; font-size:14px; color:#8d6e63; font-style:italic; margin:20px 0 0;">
                    "…that you may be filled with the knowledge of His will in all wisdom and spiritual understanding." — Colossians 1:9
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


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _section(title: str, body_html: str) -> str:
    return f'<section style="margin:12px 0;"><h3 style="margin:0 0 6px 0;">{_esc(title)}</h3>{body_html}</section>'


def assemble_email_html(study_url: str, prayer_title: str, prayer_topics: list[str]) -> str:
    study_html = (
        f'<p style="margin:0;">📖 <a href="{_esc(study_url)}">'
        "Read today's study at study.coviecraft.dev</a></p>"
    )
    # quote=False keeps apostrophes literal (e.g. "Lord's Day"); safe in element body text.
    items = "".join(f"<li>{html.escape(t, quote=False)}</li>" for t in prayer_topics)
    prayer_html = f"<ul>{items}</ul>"
    return (
        _HEADER
        + _section("Today's Study", study_html)
        + _section(f"Prayer Focus — {prayer_title}", prayer_html)
    )
