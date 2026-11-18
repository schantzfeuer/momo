from __future__ import annotations

import html
from typing import Any, Sequence


def escape(value: Any) -> str:
    return html.escape(str(value))


def detail_block(title: str, content: str, note: str = "") -> str:
    safe_note = f'<span class="detail-note">{escape(note)}</span>' if note else ""
    return (
        '<details class="detail-block">'
        f"<summary>{escape(title)}{safe_note}</summary>"
        f'<div class="detail-content">{content}</div></details>'
    )


def table(headers: Sequence[str], rows: Sequence[str], empty: str = "No data available.") -> str:
    if not rows:
        return f'<p class="muted">{escape(empty)}</p>'
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f"{header_html}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )
