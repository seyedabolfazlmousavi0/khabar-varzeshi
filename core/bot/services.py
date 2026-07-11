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

from core.bot.text_compose import format_link_html, parse_telegram_text
from core.models import NewsArticle

logger = logging.getLogger(__name__)

PENDING_BATCH_SIZE = 20
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096
_TRUNCATION_SUFFIX = "\n\n… (ادامهٔ متن حذف شد)"

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


def _format_telegram_preview(raw: str | None) -> str:
    """Render telegram_text for the admin preview (body escaped, link as HTML)."""
    parsed = parse_telegram_text(raw)
    segments: list[str] = []
    if parsed.body:
        segments.append(html.escape(parsed.body))
    if parsed.link_url:
        segments.append(format_link_html(parsed.link_url))
    if parsed.footer:
        segments.append(html.escape(parsed.footer))
    return "\n\n".join(segments) if segments else "—"


def _truncate_telegram_html(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    keep = max_length - len(_TRUNCATION_SUFFIX)
    if keep <= 0:
        return text[:max_length]
    return text[:keep].rstrip() + _TRUNCATION_SUFFIX


def format_article_message(
    article: NewsArticle,
    *,
    max_length: int | None = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    title = html.escape(article.site_title or article.original_title or "—")
    lead = html.escape(article.site_lead or "—")
    site_body = html.escape((article.site_body or "").strip() or "—")
    telegram_text = _format_telegram_preview(article.telegram_text)

    header = (
        f"📰 <b>تیتر:</b> {title}\n\n"
        f"📝 <b>لید:</b> {lead}\n\n"
        f"📄 <b>متن سایت:</b>\n{site_body}\n\n"
        f"📱 <b>تلگرام:</b>\n{telegram_text}"
    )

    if max_length is None:
        return header

    if len(header) <= max_length:
        return header

    # Preserve title/lead/telegram; shrink the site-body block to fit.
    prefix = (
        f"📰 <b>تیتر:</b> {title}\n\n"
        f"📝 <b>لید:</b> {lead}\n\n"
        f"📄 <b>متن سایت:</b>\n"
    )
    suffix = f"\n\n📱 <b>تلگرام:</b>\n{telegram_text}"
    body_budget = max_length - len(prefix) - len(suffix) - len(_TRUNCATION_SUFFIX)
    if body_budget > 0:
        trimmed_body = site_body[:body_budget].rstrip() + _TRUNCATION_SUFFIX
        return prefix + trimmed_body + suffix

    return _truncate_telegram_html(header, max_length)


def format_review_message(article: NewsArticle, *, is_photo: bool = False) -> str:
    """Format an operator preview respecting Telegram caption limits."""
    limit = TELEGRAM_CAPTION_LIMIT if is_photo else TELEGRAM_MESSAGE_LIMIT
    return format_article_message(article, max_length=limit)


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


async def refresh_article_preview(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    article: NewsArticle,
    reply_markup: InlineKeyboardMarkup | None,
    is_photo: bool = False,
) -> None:
    """Refresh a pending-article preview by chat/message id."""
    preview_text = format_review_message(article, is_photo=is_photo)
    try:
        await bot.edit_message_text(
            text=preview_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except TelegramAPIError:
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=preview_text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to refresh article preview: %r", exc)


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
