"""Background site-publishing jobs triggered from the Telegram bot."""

from __future__ import annotations

import asyncio
import logging
from threading import Lock

from aiogram import Bot
from asgiref.sync import sync_to_async
from django.db import close_old_connections

from core.bot.keyboards import article_review_keyboard
from core.bot.review_panels import sync_review_panels
from core.bot.services import load_article_for_bot
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
) -> None:
    """Publish one article on the site and sync every admin's review panel."""
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
            try:
                article = await sync_to_async(load_article_for_bot)(article_id)
            except Exception:
                return
            await sync_review_panels(
                bot,
                article_id,
                reply_markup=review_keyboard_for_article(article),
                suffix="❌ <b>خطا در انتشار روی سایت.</b>",
            )
            return
        except Exception:
            logger.exception(
                "Unexpected site publish failure for article #%s",
                article_id,
            )
            try:
                article = await sync_to_async(load_article_for_bot)(article_id)
            except Exception:
                return
            await sync_review_panels(
                bot,
                article_id,
                reply_markup=review_keyboard_for_article(article),
                suffix="❌ <b>خطا در انتشار روی سایت.</b>",
            )
            return

        _mark_site_published(article_id)
        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except Exception:
            logger.warning(
                "Could not reload article #%s after successful site publish.",
                article_id,
            )
            return

        await sync_review_panels(
            bot,
            article_id,
            reply_markup=review_keyboard_for_article(article),
            suffix="✅ <b>روی سایت منتشر شد.</b>",
            site_published=True,
        )
    finally:
        _unmark_in_progress(article_id)
