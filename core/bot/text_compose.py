"""Parse and compose Telegram channel post text (body, link block, footer)."""

from __future__ import annotations

import re
from dataclasses import dataclass

SITE_LINK_ANCHOR = "در سایت خبرورزشی بخوانید"
DEFAULT_FOOTER = "@KhabarVarzeshi"

_LINK_HTML_RE = re.compile(
    rf'<a\s+href=["\']([^"\']+)["\']\s*>\s*{re.escape(SITE_LINK_ANCHOR)}\s*</a>',
    re.IGNORECASE,
)
_FOOTER_RE = re.compile(r"^@\w+\s*$")
_BARE_URL_LINE_RE = re.compile(r"^https?://\S+\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TelegramTextParts:
    body: str
    link_url: str | None
    footer: str


def format_link_html(url: str) -> str:
    return f'<a href="{url.strip()}">{SITE_LINK_ANCHOR}</a>'


def parse_telegram_text(raw: str | None) -> TelegramTextParts:
    """Split stored telegram_text into editable body, optional link URL, and footer."""
    text = (raw or "").strip()
    if not text:
        return TelegramTextParts(body="", link_url=None, footer=DEFAULT_FOOTER)

    link_url: str | None = None
    link_match = _LINK_HTML_RE.search(text)
    if link_match:
        link_url = link_match.group(1).strip()
        text = _LINK_HTML_RE.sub("", text).strip()

    lines = [line for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()

    footer = DEFAULT_FOOTER
    if lines and _FOOTER_RE.match(lines[-1].strip()):
        footer = lines[-1].strip()
        lines = lines[:-1]
        while lines and not lines[-1].strip():
            lines.pop()

    # Migrate legacy posts that appended a bare URL before the footer.
    while lines and _BARE_URL_LINE_RE.match(lines[-1].strip()):
        if link_url is None:
            link_url = lines[-1].strip()
        lines = lines[:-1]
        while lines and not lines[-1].strip():
            lines.pop()

    body = "\n".join(lines).strip()
    return TelegramTextParts(body=body, link_url=link_url, footer=footer)


def compose_telegram_text(
    body: str,
    *,
    link_url: str | None = None,
    footer: str | None = None,
) -> str:
    """Build final publishable telegram_text with link before footer."""
    parts: list[str] = []
    cleaned_body = body.strip()
    if cleaned_body:
        parts.append(cleaned_body)
    if link_url:
        parts.append(format_link_html(link_url))
    cleaned_footer = (footer or DEFAULT_FOOTER).strip()
    if cleaned_footer:
        parts.append(cleaned_footer)
    return "\n\n".join(parts)


def inject_site_link(raw: str | None, url: str) -> str:
    """Replace or add the fixed-anchor link immediately before the footer."""
    parsed = parse_telegram_text(raw)
    return compose_telegram_text(
        parsed.body,
        link_url=url.strip(),
        footer=parsed.footer,
    )


def normalize_telegram_text(raw: str | None) -> str:
    """Re-compose text so link placement and footer order are canonical."""
    parsed = parse_telegram_text(raw)
    return compose_telegram_text(
        parsed.body,
        link_url=parsed.link_url,
        footer=parsed.footer,
    )


def get_editable_body(raw: str | None) -> str:
    """Return only the main post body (excludes link block and footer)."""
    return parse_telegram_text(raw).body
