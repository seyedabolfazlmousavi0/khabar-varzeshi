"""Editorial Telegram bot for approving/rejecting pending NewsArticle rows.

Run with:

    python manage.py run_bot

Long-polls Telegram, listens for /check_pending from the configured admin
chat, and offers approve/reject inline buttons for each pending article.
"""

from __future__ import annotations

import html
import logging
import os
import time
from typing import Any

import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection
from dotenv import load_dotenv

from core.models import NewsArticle
import socket
import urllib3.util.connection as urllib3_cn

# این خط باعث می‌شود پایتون کلاً IPv6 را نادیده بگیرد
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

logger = logging.getLogger(__name__)

PENDING_BATCH_SIZE = 5

# Telegram caption character limit for send_photo (1024 chars per BotAPI).
TELEGRAM_CAPTION_LIMIT = 1024

# Callback-data prefixes for the inline keyboard.
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"

# Fields the bot needs on every article load (explicit list for refresh_from_db).
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


def _normalized_image_url(article: NewsArticle) -> str | None:
    """Return a stripped image URL, or None if empty."""
    url = (article.image_url or "").strip()
    return url or None


def _raw_image_url_from_db(article_id: int) -> str | None:
    """Read image_url directly from the DB (bypasses ORM instance cache)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT image_url FROM core_newsarticle WHERE id = %s",
            [article_id],
        )
        row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0]).strip() or None


def _load_article_for_bot(article_id: int) -> NewsArticle:
    """Load a single article with a fresh DB read (thread-safe for telebot).

    telebot handlers run in worker threads. ``close_old_connections()`` plus
    ``refresh_from_db()`` ensures we never serve a stale in-memory instance
    that was loaded before ``image_url`` was written by the worker process.
    """
    close_old_connections()
    article = (
        NewsArticle.objects.using("default")
        .select_related("source")
        .get(pk=article_id)
    )
    article.refresh_from_db(fields=_ARTICLE_BOT_FIELDS)
    return article


def _load_pending_articles(batch_size: int) -> list[NewsArticle]:
    """Return up to ``batch_size`` oldest pending articles, freshly loaded."""
    close_old_connections()
    pending_ids = list(
        NewsArticle.objects.using("default")
        .filter(status=NewsArticle.Status.PENDING)
        .order_by("created_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    return [_load_article_for_bot(pk) for pk in pending_ids]


def _resolve_image_url(article: NewsArticle) -> str | None:
    """Return the best available image URL, with ORM-vs-DB fallback."""
    image_url = _normalized_image_url(article)
    if image_url:
        return image_url

    raw_url = _raw_image_url_from_db(article.id)
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


def _send_with_optional_image(
    bot: telebot.TeleBot,
    chat_id: Any,
    text: str,
    image_url: str | None,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
):
    """Send `text` to `chat_id`, attaching `image_url` as a photo when present.

    Strategy:
      • image present, text fits in 1024 chars  → send_photo + caption
      • image present, text > 1024 chars        → send_photo (no caption)
                                                  + send_message (full text)
      • no image                                → send_message
      • send_photo rejected by Telegram         → fall back to send_message
        (e.g. dead URL, wrong format, file too large)
    """
    if image_url:
        try:
            if len(text) <= TELEGRAM_CAPTION_LIMIT:
                logger.info(
                    "send_photo with caption | chat=%s | url=%s | text=%d chars",
                    chat_id, image_url, len(text),
                )
                return bot.send_photo(
                    chat_id,
                    image_url,
                    caption=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )

            logger.info(
                "send_photo (no caption: text=%d > %d) + follow-up text | "
                "chat=%s | url=%s",
                len(text), TELEGRAM_CAPTION_LIMIT, chat_id, image_url,
            )
            bot.send_photo(chat_id, image_url)
            return bot.send_message(
                chat_id,
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
        except ApiTelegramException as exc:
            logger.warning(
                "send_photo failed (%r) for url=%s — falling back to text-only.",
                exc, image_url,
            )

    return bot.send_message(
        chat_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


def _build_keyboard(article_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton(
            "✅ تایید و انتشار",
            callback_data=f"{ACTION_APPROVE}_{article_id}",
        ),
        InlineKeyboardButton(
            "❌ رد خبر",
            callback_data=f"{ACTION_REJECT}_{article_id}",
        ),
    )
    return keyboard


def _format_article_message(article: NewsArticle) -> str:
    """Persian-styled HTML preview of a pending article."""
    title = html.escape(article.site_title or article.original_title or "—")
    lead = html.escape(article.site_lead or "—")
    telegram_text = html.escape(article.telegram_text or "—")
    return (
        f"📰 <b>تیتر:</b> {title}\n\n"
        f"📝 <b>لید:</b> {lead}\n\n"
        f"📱 <b>تلگرام:</b>\n{telegram_text}"
    )


class Command(BaseCommand):
    help = (
        "Run the editorial Telegram bot (long polling). "
        "Use /check_pending in the admin chat to review pending articles."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        load_dotenv(settings.BASE_DIR / ".env")

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin_id_raw = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
        public_channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID")

        if not token or token == "your_bot_token_here":
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in .env.")
        if not admin_id_raw or admin_id_raw == "your_chat_id_here":
            raise CommandError("TELEGRAM_ADMIN_CHAT_ID is not set in .env.")
        if not public_channel or public_channel == "@YourPublicChannel":
            raise CommandError(
                "TELEGRAM_PUBLIC_CHANNEL_ID is not set in .env. "
                "Add the @username or numeric id of the public channel."
            )

        try:
            admin_chat_id = int(admin_id_raw)
        except ValueError as exc:
            raise CommandError(
                "TELEGRAM_ADMIN_CHAT_ID must be a numeric chat id."
            ) from exc

        bot = telebot.TeleBot(token, parse_mode="HTML")

        self.stdout.write(
            self.style.HTTP_INFO(
                f"Database: {settings.DATABASES['default']['NAME']}"
            )
        )

        def _is_admin(chat_id: int) -> bool:
            return chat_id == admin_chat_id

        @bot.message_handler(commands=["start", "help"])
        def cmd_start(message: Message) -> None:
            if not _is_admin(message.chat.id):
                return
            close_old_connections()
            bot.reply_to(
                message,
                "سلام! من ربات سردبیر اخبار ورزشی هستم.\n\n"
                "برای دیدن اخبار در انتظار تایید از دستور /check_pending استفاده کن.",
            )

        @bot.message_handler(commands=["check_pending"])
        def cmd_check_pending(message: Message) -> None:
            if not _is_admin(message.chat.id):
                return

            pending = _load_pending_articles(PENDING_BATCH_SIZE)

            if not pending:
                bot.send_message(
                    message.chat.id,
                    "هیچ خبر در انتظار تاییدی وجود ندارد.",
                )
                return

            bot.send_message(
                message.chat.id,
                f"📋 <b>{len(pending)}</b> خبر در انتظار تایید:",
            )

            for article in pending:
                image_url = _resolve_image_url(article)
                self.stdout.write(
                    f"  preview article id={article.id} "
                    f"image_url={image_url or 'NONE'}"
                )
                try:
                    _send_with_optional_image(
                        bot,
                        message.chat.id,
                        _format_article_message(article),
                        image_url,
                        reply_markup=_build_keyboard(article.id),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to send article %s: %r", article.id, exc
                    )
                    self.stderr.write(
                        self.style.WARNING(
                            f"Failed to send article {article.id}: {exc!r}"
                        )
                    )

        @bot.callback_query_handler(func=lambda call: True)
        def on_callback(call: CallbackQuery) -> None:
            if call.message is None or not _is_admin(call.message.chat.id):
                bot.answer_callback_query(call.id, "اجازه دسترسی ندارید.")
                return

            action, _, raw_id = (call.data or "").partition("_")
            try:
                article_id = int(raw_id)
            except ValueError:
                bot.answer_callback_query(call.id, "داده نامعتبر.")
                return

            try:
                article = _load_article_for_bot(article_id)
            except NewsArticle.DoesNotExist:
                bot.answer_callback_query(call.id, "این خبر در پایگاه داده پیدا نشد.")
                self._safe_edit(
                    bot,
                    call,
                    suffix="⚠️ این خبر دیگر در پایگاه داده وجود ندارد.",
                )
                return

            if action == ACTION_APPROVE:
                image_url = _resolve_image_url(article)
                self.stdout.write(
                    f"  publish article id={article.id} "
                    f"image_url={image_url or 'NONE'} "
                    f"text={len(article.telegram_text or '')} chars"
                )
                try:
                    _send_with_optional_image(
                        bot,
                        os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID"),
                        article.telegram_text or "",
                        image_url,
                    )
                except ApiTelegramException as exc:
                    logger.warning(
                        "Failed to publish article %s to public channel: %r",
                        article.id, exc,
                    )
                    bot.answer_callback_query(
                        call.id,
                        "❌ ارسال به کانال ناموفق بود.",
                    )
                    bot.send_message(
                        call.message.chat.id,
                        f"❌ خطا در ارسال به کانال عمومی: {str(exc)}",
                    )
                else:
                    article.status = NewsArticle.Status.PUBLISHED
                    article.save(update_fields=["status"])
                    bot.answer_callback_query(call.id, "✅ خبر تایید شد.")
                    self._safe_edit(
                        bot,
                        call,
                        suffix="✅ <b>این خبر تایید و منتشر شد.</b>",
                    )

            elif action == ACTION_REJECT:
                article.status = NewsArticle.Status.REJECTED
                article.save(update_fields=["status"])
                bot.answer_callback_query(call.id, "❌ خبر رد شد.")
                self._safe_edit(
                    bot,
                    call,
                    suffix="❌ <b>این خبر توسط سردبیر رد شد.</b>",
                )

            else:
                bot.answer_callback_query(call.id, "اقدام ناشناخته.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Bot is up. Authorized admin chat id: {admin_chat_id}. "
                "Press Ctrl+C to stop."
            )
        )

        try:
            while True:
                try:
                    bot.infinity_polling(timeout=60, long_polling_timeout=60)
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Network error: {e}. Restarting bot in 10 seconds..."
                        )
                    )
                    time.sleep(10)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nBot stopped by user."))

    @staticmethod
    def _safe_edit(
        bot: telebot.TeleBot,
        call: CallbackQuery,
        *,
        suffix: str,
    ) -> None:
        """Replace the buttons with a status line, preserving the original text."""
        original = ""
        if call.message is not None:
            # `html_text` rebuilds the formatted body from message entities.
            original = getattr(call.message, "html_text", None) or call.message.text or ""

        new_text = f"{original}\n\n{suffix}".strip()
        try:
            bot.edit_message_text(
                new_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Failed to edit message: %r", exc)
