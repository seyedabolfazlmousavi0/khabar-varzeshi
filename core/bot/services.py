"""Shared DB and Telegram helpers for the editorial bot."""

from __future__ import annotations

import html
import logging
import re
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
DIGEST_PAGE_SIZE = 10
DIGEST_LOOKBACK_HOURS = 24
DIGEST_LEAD_MAX_CHARS = 160
DIGEST_HEADER = "۱۰ خبر برگزیده ۲۴ ساعت اخیر از منابع منتخب"
NEWS_DEEP_LINK_PREFIX = "news_"
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096
_TRUNCATION_SUFFIX = "\n\n… (ادامهٔ متن حذف شد)"
_TAG_RE = re.compile(r"<[^>]+>")

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


def to_persian_digits(value: int | str) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


def load_pending_digest_page(
    page: int = 0,
    *,
    page_size: int = DIGEST_PAGE_SIZE,
    lookback_hours: int = DIGEST_LOOKBACK_HOURS,
) -> tuple[list[NewsArticle], int]:
    """Return one page of recent pending articles and the total matching count."""
    from datetime import timedelta

    from django.utils import timezone

    close_old_connections()
    page = max(0, int(page))
    page_size = max(1, int(page_size))
    since = timezone.now() - timedelta(hours=lookback_hours)

    qs = (
        NewsArticle.objects.using("default")
        .filter(
            status=NewsArticle.Status.PENDING,
            created_at__gte=since,
        )
        .order_by("-created_at")
    )
    total = qs.count()
    offset = page * page_size
    pending_ids = list(qs.values_list("pk", flat=True)[offset : offset + page_size])
    return [load_article_for_bot(pk) for pk in pending_ids], total


def _truncate_plain(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if max_chars <= 0 or not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _telegram_href(url: str) -> str | None:
    """Return an HTML-escaped href safe for Telegram parse_mode=HTML, or None."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return html.escape(raw, quote=True)


def format_digest_message(
    articles: list[NewsArticle],
    *,
    page: int = 0,
    bot_username: str | None = None,
    page_size: int = DIGEST_PAGE_SIZE,
) -> str:
    """Build the numbered digest list shown to admins.

    Never hard-truncates the final HTML string (that breaks Telegram ``<a>`` tags).
    Fits under the message limit by shortening leads instead.
    """
    start_index = page * page_size

    def _build(lead_max: int) -> str:
        lines: list[str] = [DIGEST_HEADER, ""]
        for offset, article in enumerate(articles):
            number = to_persian_digits(start_index + offset + 1)
            title = (article.site_title or article.original_title or "بدون تیتر").strip()
            # Strip characters that can confuse Telegram HTML even after escape.
            title = title.replace("\n", " ").replace("\r", " ")
            safe_title = html.escape(title, quote=False)

            try:
                source_name = html.escape(
                    (article.source.name or "").strip() or "منبع",
                    quote=False,
                )
            except Exception:
                source_name = "منبع"

            if bot_username:
                deep_link = (
                    f"https://t.me/{bot_username}"
                    f"?start={NEWS_DEEP_LINK_PREFIX}{article.id}"
                )
                title_html = (
                    f'<a href="{html.escape(deep_link, quote=True)}">{safe_title}</a>'
                )
            else:
                title_html = f"<b>{safe_title}</b>"

            original_url = (article.original_url or "").strip()
            href = _telegram_href(original_url)
            if href:
                # Keep URL visible, but escape entity-sensitive chars (&, <, >).
                url_label = html.escape(original_url, quote=False)
                url_html = f'(<a href="{href}">{url_label}</a>)'
            elif original_url:
                url_html = f"({html.escape(original_url, quote=False)})"
            else:
                url_html = "(—)"

            lead = _truncate_plain(article.site_lead or "", lead_max)
            lead_html = html.escape(lead, quote=False) if lead else "—"

            lines.append(f"{number}- {title_html} {url_html} / {source_name}")
            lines.append(lead_html)
            lines.append("")

        return "\n".join(lines).rstrip()

    for lead_max in (
        DIGEST_LEAD_MAX_CHARS,
        120,
        80,
        40,
        0,
    ):
        text = _build(lead_max)
        if len(text) <= TELEGRAM_MESSAGE_LIMIT:
            return text

    # Absolute fallback: titles only, no original-url anchors (plain escaped URL).
    lines = [DIGEST_HEADER, ""]
    for offset, article in enumerate(articles):
        number = to_persian_digits(start_index + offset + 1)
        title = html.escape(
            (article.site_title or article.original_title or "بدون تیتر").strip(),
            quote=False,
        )
        try:
            source_name = html.escape(
                (article.source.name or "").strip() or "منبع",
                quote=False,
            )
        except Exception:
            source_name = "منبع"
        original_url = html.escape((article.original_url or "").strip() or "—", quote=False)
        if bot_username:
            deep_link = (
                f"https://t.me/{bot_username}"
                f"?start={NEWS_DEEP_LINK_PREFIX}{article.id}"
            )
            title_html = f'<a href="{html.escape(deep_link, quote=True)}">{title}</a>'
        else:
            title_html = f"<b>{title}</b>"
        lines.append(f"{number}- {title_html} ({original_url}) / {source_name}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def send_article_review_card(
    bot: Bot,
    chat_id: Any,
    article: NewsArticle,
) -> Message | None:
    """Send the full review card (preview + action buttons) for one article."""
    from asgiref.sync import sync_to_async

    from core.bot.review_panels import register_review_panel
    from core.bot.site_publish import review_keyboard_for_article

    image_url = await sync_to_async(resolve_image_url)(article)
    preview_text = await sync_to_async(format_review_message)(
        article,
        is_photo=bool(image_url),
    )
    sent = await send_with_optional_image(
        bot,
        chat_id,
        preview_text,
        image_url,
        reply_markup=review_keyboard_for_article(article),
    )
    if sent is not None:
        register_review_panel(
            article.id,
            sent.chat.id,
            sent.message_id,
            is_photo=bool(sent.photo),
        )
    return sent


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


def _format_source_link(article: NewsArticle) -> str:
    """Source name as HTML anchor pointing at the original scraped article URL."""
    source_name = ""
    try:
        source_name = (article.source.name or "").strip()
    except Exception:
        source_name = ""
    if not source_name:
        source_name = "منبع"

    href = (article.original_url or "").strip()
    if not href:
        return f"🔗 {html.escape(source_name)}"

    safe_href = html.escape(href, quote=True)
    safe_name = html.escape(source_name)
    return f'🔗 <a href="{safe_href}">{safe_name}</a>'


def format_article_message(
    article: NewsArticle,
    *,
    max_length: int | None = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    title = html.escape(article.site_title or article.original_title or "—")
    lead = html.escape(article.site_lead or "—")
    site_body = html.escape((article.site_body or "").strip() or "—")
    telegram_text = _format_telegram_preview(article.telegram_text)
    source_link = _format_source_link(article)

    header = (
        f"📰 <b>تیتر:</b> {title}\n\n"
        f"📝 <b>لید:</b> {lead}\n\n"
        f"📄 <b>متن سایت:</b>\n{site_body}\n\n"
        f"📱 <b>تلگرام:</b>\n{telegram_text}\n\n"
        f"{source_link}"
    )

    if max_length is None:
        return header

    if len(header) <= max_length:
        return header

    # Preserve title/lead/telegram/source; shrink the site-body block to fit.
    prefix = (
        f"📰 <b>تیتر:</b> {title}\n\n"
        f"📝 <b>لید:</b> {lead}\n\n"
        f"📄 <b>متن سایت:</b>\n"
    )
    suffix = f"\n\n📱 <b>تلگرام:</b>\n{telegram_text}\n\n{source_link}"
    body_budget = max_length - len(prefix) - len(suffix) - len(_TRUNCATION_SUFFIX)
    if body_budget > 0:
        trimmed_body = site_body[:body_budget].rstrip() + _TRUNCATION_SUFFIX
        return prefix + trimmed_body + suffix

    return _truncate_telegram_html(header, max_length)


def format_review_message(article: NewsArticle, *, is_photo: bool = False) -> str:
    """Format an operator preview respecting Telegram caption limits."""
    limit = TELEGRAM_CAPTION_LIMIT if is_photo else TELEGRAM_MESSAGE_LIMIT
    return format_article_message(article, max_length=limit)


def _site_body_as_plain_text(raw_html: str | None) -> str:
    """Convert site HTML body to readable plain text for the full-site view."""
    text = (raw_html or "").strip()
    if not text:
        return "—"
    # Preserve paragraph/heading breaks before stripping tags.
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or "—"


def format_full_site_version(article: NewsArticle) -> str:
    """Full site title/lead/body for the 'view full site' button (no length trim)."""
    title = html.escape(article.site_title or article.original_title or "—")
    lead = html.escape(article.site_lead or "—")
    body = html.escape(_site_body_as_plain_text(article.site_body))
    source_link = _format_source_link(article)
    return (
        f"📄 <b>نسخه کامل سایت — خبر #{article.id}</b>\n\n"
        f"📰 <b>تیتر:</b>\n{title}\n\n"
        f"📝 <b>لید:</b>\n{lead}\n\n"
        f"📄 <b>متن:</b>\n{body}\n\n"
        f"{source_link}"
    )


def split_telegram_chunks(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split long text into Telegram-safe message chunks."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


async def send_full_site_version(
    bot: Bot,
    chat_id: Any,
    article: NewsArticle,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    """Send the complete site version, splitting across messages if needed."""
    full_text = format_full_site_version(article)
    for index, chunk in enumerate(split_telegram_chunks(full_text)):
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if index == 0 and reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        await bot.send_message(**kwargs)


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
