"""Track per-admin review preview messages so status changes sync to everyone."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from asgiref.sync import sync_to_async

from core.bot.services import (
    format_review_message,
    load_article_for_bot,
    update_review_message,
)
from core.models import NewsArticle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewPanelRef:
    chat_id: int
    message_id: int
    is_photo: bool


_lock = Lock()
# article_id -> {(chat_id, message_id): ReviewPanelRef}
_panels: dict[int, dict[tuple[int, int], ReviewPanelRef]] = {}


def register_review_panel(
    article_id: int,
    chat_id: int,
    message_id: int,
    *,
    is_photo: bool = False,
) -> None:
    """Remember a preview message so later approve/reject/publish can update it."""
    key = (chat_id, message_id)
    with _lock:
        bucket = _panels.setdefault(article_id, {})
        bucket[key] = ReviewPanelRef(
            chat_id=chat_id,
            message_id=message_id,
            is_photo=is_photo,
        )


def list_review_panels(article_id: int) -> list[ReviewPanelRef]:
    with _lock:
        return list(_panels.get(article_id, {}).values())


def pop_review_panels(article_id: int) -> list[ReviewPanelRef]:
    """Return and clear all tracked panels for an article."""
    with _lock:
        bucket = _panels.pop(article_id, {})
        return list(bucket.values())


def unregister_review_panel(article_id: int, chat_id: int, message_id: int) -> None:
    with _lock:
        bucket = _panels.get(article_id)
        if not bucket:
            return
        bucket.pop((chat_id, message_id), None)
        if not bucket:
            _panels.pop(article_id, None)


def build_publish_status_suffix(article: NewsArticle, *, site_published: bool) -> str | None:
    """Human-readable publish destinations for admin previews."""
    if article.status == NewsArticle.Status.REJECTED:
        return "❌ <b>رد شد.</b>"

    parts: list[str] = []
    if article.status == NewsArticle.Status.PUBLISHED:
        parts.append("✅ کانال تلگرام")
    if site_published:
        parts.append("✅ سایت خبرورزشی")

    if not parts:
        return None
    return "📍 <b>وضعیت انتشار:</b> " + " | ".join(parts)


class _MessageProxy:
    """Minimal message-like object for update_review_message."""

    def __init__(self, chat_id: int, message_id: int, is_photo: bool) -> None:
        self.chat = type("Chat", (), {"id": chat_id})()
        self.message_id = message_id
        self.photo = [object()] if is_photo else None


async def sync_review_panels(
    bot: Bot,
    article_id: int,
    *,
    reply_markup=None,
    suffix: str | None = None,
    include_status_suffix: bool = True,
    site_published: bool | None = None,
) -> None:
    """Refresh every admin's preview for this article with the same status/keyboard."""
    panels = list_review_panels(article_id)
    if not panels:
        return

    try:
        article = await sync_to_async(load_article_for_bot)(article_id)
    except NewsArticle.DoesNotExist:
        logger.warning("sync_review_panels: article #%s missing", article_id)
        return

    if site_published is None:
        from core.bot.site_publish import is_site_published

        site_published = is_site_published(article_id)

    status_line = (
        build_publish_status_suffix(article, site_published=site_published)
        if include_status_suffix
        else None
    )
    if suffix and status_line:
        combined_suffix = f"{suffix}\n{status_line}"
    else:
        combined_suffix = suffix or status_line

    for panel in panels:
        preview_text = await sync_to_async(format_review_message)(
            article,
            is_photo=panel.is_photo,
        )
        await update_review_message(
            bot,
            _MessageProxy(panel.chat_id, panel.message_id, panel.is_photo),
            preview_text,
            reply_markup=reply_markup,
            suffix=combined_suffix,
        )


async def remove_review_panels(
    bot: Bot,
    article_id: int,
    *,
    fallback_suffix: str = "❌ <b>رد شد.</b>",
) -> None:
    """Delete every admin's preview for a rejected article (edit if delete fails)."""
    panels = pop_review_panels(article_id)
    if not panels:
        return

    try:
        article = await sync_to_async(load_article_for_bot)(article_id)
    except NewsArticle.DoesNotExist:
        article = None

    for panel in panels:
        try:
            await bot.delete_message(panel.chat_id, panel.message_id)
            continue
        except TelegramAPIError as exc:
            logger.info(
                "Could not delete review panel article=%s chat=%s msg=%s: %r",
                article_id,
                panel.chat_id,
                panel.message_id,
                exc,
            )

        if article is None:
            continue

        preview_text = await sync_to_async(format_review_message)(
            article,
            is_photo=panel.is_photo,
        )
        await update_review_message(
            bot,
            _MessageProxy(panel.chat_id, panel.message_id, panel.is_photo),
            preview_text,
            reply_markup=None,
            suffix=fallback_suffix,
        )
