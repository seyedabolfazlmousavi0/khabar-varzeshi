"""Shared DB and Telegram helpers for the editorial bot."""

from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, Message
from django.db import close_old_connections, connection

from core.models import NewsArticle

logger = logging.getLogger(__name__)

PENDING_BATCH_SIZE = 20
TELEGRAM_CAPTION_LIMIT = 1024

_ARTICLE_BOT_FIELDS = (
    "image_url",
    "telegram_text",
    "site_title",
    "site_lead",
    "site_body",
    "status",
    "original_title",
    "original_url",
)


def normalized_image_url(article: NewsArticle) -> str | None:
    url = (article.image_url or "").strip()
    return url or None


def raw_image_url_from_db(article_id: int) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT image_url FROM core_newsarticle WHERE id = %s",
            [article_id],
        )
        row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0]).strip() or None


def load_article_for_bot(article_id: int) -> NewsArticle:
    close_old_connections()
    article = (
        NewsArticle.objects.using("default")
        .select_related("source")
        .get(pk=article_id)
    )
    article.refresh_from_db(fields=_ARTICLE_BOT_FIELDS)
    return article


def load_pending_articles(batch_size: int = PENDING_BATCH_SIZE) -> list[NewsArticle]:
    close_old_connections()
    pending_ids = list(
        NewsArticle.objects.using("default")
        .filter(status=NewsArticle.Status.PENDING)
        .order_by("-created_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    return [load_article_for_bot(pk) for pk in pending_ids]


def resolve_image_url(article: NewsArticle) -> str | None:
    image_url = normalized_image_url(article)
    if image_url:
        return image_url

    raw_url = raw_image_url_from_db(article.id)
    if raw_url:
        logger.warning(
            "article id=%s: ORM image_url was empty but DB has %r — using raw value.",
            article.id,
            raw_url,
        )
        return raw_url

    logger.info(
        "article id=%s: image_url is empty in both ORM and DB.",
        article.id,
    )
    return None


def format_article_message(article: NewsArticle) -> str:
    title = html.escape(article.site_title or article.original_title or "—")
    lead = html.escape(article.site_lead or "—")
    telegram_text = html.escape(article.telegram_text or "—")
    return (
        f"📰 <b>Title:</b> {title}\n\n"
        f"📝 <b>Lead:</b> {lead}\n\n"
        f"📱 <b>Telegram:</b>\n{telegram_text}"
    )


def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def send_with_optional_image(
    bot: Bot,
    chat_id: Any,
    text: str,
    image_url: str | None,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> Message | None:
    if image_url:
        try:
            if len(text) <= TELEGRAM_CAPTION_LIMIT:
                logger.info(
                    "send_photo with caption | chat=%s | url=%s | text=%d chars",
                    chat_id,
                    image_url,
                    len(text),
                )
                return await bot.send_photo(
                    chat_id,
                    image_url,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )

            logger.info(
                "send_photo (no caption: text=%d > %d) + follow-up text | "
                "chat=%s | url=%s",
                len(text),
                TELEGRAM_CAPTION_LIMIT,
                chat_id,
                image_url,
            )
            await bot.send_photo(chat_id, image_url)
            return await bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except TelegramAPIError as exc:
            logger.warning(
                "send_photo failed (%r) for url=%s — falling back to text-only.",
                exc,
                image_url,
            )

    return await bot.send_message(
        chat_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def update_review_message(
    bot: Bot,
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    suffix: str | None = None,
) -> None:
    """Update a review preview message (text or photo caption)."""
    body = f"{text}\n\n{suffix}".strip() if suffix else text
    try:
        if message.photo:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.message_id,
                caption=body,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            await bot.edit_message_text(
                text=body,
                chat_id=message.chat.id,
                message_id=message.message_id,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
    except TelegramAPIError as exc:
        logger.warning("Failed to update review message: %r", exc)


async def finalize_review_message(
    bot: Bot,
    message: Message,
    *,
    suffix: str,
) -> None:
    """Replace inline buttons with a final status line."""
    if message.photo:
        original = message.caption or ""
    else:
        original = message.html_text or message.text or ""
    await update_review_message(
        bot,
        message,
        original,
        reply_markup=None,
        suffix=suffix,
    )
