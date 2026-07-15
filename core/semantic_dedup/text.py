"""Text preparation for bilingual semantic deduplication."""

from __future__ import annotations

import html
import re
from typing import Any

from bs4 import BeautifulSoup

_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def build_embedding_document(*, title: str, description: str = "") -> str:
    """Build a short bilingual document that emphasizes story-specific entities.

    Title carries most of the entity signal (players, clubs, scores). Description
    adds detail without flooding the vector with generic sports vocabulary.
    """
    clean_title = strip_html(title)
    clean_description = strip_html(description)

    parts: list[str] = []
    if clean_title:
        # Repeat the title once so entity tokens weigh more than lead fluff.
        parts.append(clean_title)
        parts.append(clean_title)
    if clean_description:
        parts.append(clean_description[:1200])
    return "\n".join(parts).strip()


def entry_description(entry: Any) -> str:
    """Best-effort RSS summary for an incoming feed entry."""
    for attr in ("summary", "description"):
        value = getattr(entry, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    content_list = getattr(entry, "content", None) or []
    for item in content_list:
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value
    return ""
