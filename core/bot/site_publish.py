"""Background site-publishing jobs triggered from the Telegram bot."""

from __future__ import annotations

import asyncio
import logging
from threading import Lock

from aiogram import Bot
from asgiref.sync import sync_to_async
from django.db import close_old_connections

from core.bot.keyboards import article_review_keyboard
from core.bot.services import format_review_message, load_article_for_bot, update_review_message
from core.models import NewsArticle
from core.newsroom.exceptions import SitePublishError
from core.newsroom.publisher import publish_article_by_id

logger = logging.getLogger(__name__)

_in_progress: set[int] = set()
_site_published: set[int] = set()
_state_lock = Lock()


def is_site_publish_in_progress(article_id: int) -> bool:
    with _state_lock:
        return article_id in _in_progress


def is_site_published(article_id: int) -> bool:
    with _state_lock:
        return article_id in _site_published


def _mark_in_progress(article_id: int) -> bool:
    with _state_lock:
        if article_id in _in_progress:
            return False
        _in_progress.add(article_id)
        return True


def _unmark_in_progress(article_id: int) -> None:
    with _state_lock:
        _in_progress.discard(article_id)


def _mark_site_published(article_id: int) -> None:
    with _state_lock:
        _site_published.add(article_id)


def _publish_sync(article_id: int) -> None:
    close_old_connections()
    publish_article_by_id(article_id)


def review_keyboard_for_article(
    article: NewsArticle,
    *,
    publishing: bool = False,
):
    """Build the review inline keyboard reflecting current publish state."""
    return article_review_keyboard(
        article.id,
        publishing=publishing,
        channel_published=article.status == NewsArticle.Status.PUBLISHED,
        site_published=is_site_published(article.id),
    )


async def run_site_publish_job(
    *,
    bot: Bot,
    article_id: int,
    review_chat_id: int,
    review_message_id: int,
    review_is_photo: bool = False,
) -> None:
    """Publish one article on the site without blocking the bot event loop."""
    if not _mark_in_progress(article_id):
        logger.info(
            "Site publish for article #%s already in progress — skipping duplicate job.",
            article_id,
        )
        return

    try:
        try:
            await asyncio.to_thread(_publish_sync, article_id)
        except SitePublishError as exc:
            logger.exception(
                "Site publish failed for article #%s: %s",
                article_id,
                exc,
            )
            await _refresh_review_after_publish(
                bot,
                article_id=article_id,
                review_chat_id=review_chat_id,
                review_message_id=review_message_id,
                review_is_photo=review_is_photo,
                suffix="❌ <b>خطا در انتشار روی سایت.</b>",
            )
            return
        except Exception:
            logger.exception(
                "Unexpected site publish failure for article #%s",
                article_id,
            )
            await _refresh_review_after_publish(
                bot,
                article_id=article_id,
                review_chat_id=review_chat_id,
                review_message_id=review_message_id,
                review_is_photo=review_is_photo,
                suffix="❌ <b>خطا در انتشار روی سایت.</b>",
            )
            return

        _mark_site_published(article_id)
        await _refresh_review_after_publish(
            bot,
            article_id=article_id,
            review_chat_id=review_chat_id,
            review_message_id=review_message_id,
            review_is_photo=review_is_photo,
            suffix="✅ <b>روی سایت منتشر شد.</b>",
        )
    finally:
        _unmark_in_progress(article_id)


async def _refresh_review_after_publish(
    bot: Bot,
    *,
    article_id: int,
    review_chat_id: int,
    review_message_id: int,
    review_is_photo: bool,
    suffix: str,
) -> None:
    try:
        article = await sync_to_async(load_article_for_bot)(article_id)
    except Exception:
        logger.warning(
            "Could not reload article #%s while refreshing review message.",
            article_id,
        )
        return

    preview_text = await sync_to_async(format_review_message)(
        article,
        is_photo=review_is_photo,
    )

    class _ReviewMessageProxy:
        def __init__(self, chat_id: int, message_id: int, is_photo: bool) -> None:
            self.chat = type("Chat", (), {"id": chat_id})()
            self.message_id = message_id
            self.photo = [object()] if is_photo else None

    await update_review_message(
        bot,
        _ReviewMessageProxy(review_chat_id, review_message_id, review_is_photo),
        preview_text,
        reply_markup=review_keyboard_for_article(article),
        suffix=suffix,
    )
