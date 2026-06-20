"""FSM message handlers for editing article text and appending links."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import admin_main_menu, article_review_keyboard
from core.bot.services import (
    format_article_message,
    is_valid_http_url,
    load_article_for_bot,
)
from core.bot.states import AddLinkStates, EditNewsStates
from core.models import NewsArticle

logger = logging.getLogger(__name__)


async def _update_preview_after_change(
    message: Message,
    state: FSMContext,
    article: NewsArticle,
) -> None:
    data = await state.get_data()
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")
    if not preview_chat_id or not preview_message_id:
        return

    preview_text = await sync_to_async(format_article_message)(article)
    keyboard = article_review_keyboard(article.id)
    try:
        await message.bot.edit_message_text(
            text=preview_text,
            chat_id=preview_chat_id,
            message_id=preview_message_id,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await message.bot.edit_message_caption(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                caption=preview_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.warning("Failed to refresh article preview: %r", exc)


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="fsm")

    @router.message(EditNewsStates.waiting_for_text, admin_filter)
    async def receive_edited_text(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("Please send plain text. /cancel to abort.")
            return

        data = await state.get_data()
        article_id = data.get("article_id")
        if not article_id:
            await state.clear()
            await message.answer(
                "Session expired. Use Check Pending again.",
                reply_markup=admin_main_menu(),
            )
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await state.clear()
            await message.answer(
                "Article not found.",
                reply_markup=admin_main_menu(),
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await state.clear()
            await message.answer(
                "This article is no longer pending.",
                reply_markup=admin_main_menu(),
            )
            return

        article.telegram_text = message.text.strip()
        await sync_to_async(article.save)(update_fields=["telegram_text"])
        await _update_preview_after_change(message, state, article)
        await state.clear()
        await message.answer(
            f"✅ Telegram text updated for article #{article_id}.",
            reply_markup=admin_main_menu(),
        )

    @router.message(AddLinkStates.waiting_for_url, admin_filter)
    async def receive_link_url(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("Please send a URL. /cancel to abort.")
            return

        url = message.text.strip()
        if not is_valid_http_url(url):
            await message.answer(
                "Invalid URL. Send a link starting with http:// or https://, "
                "or /cancel to abort.",
            )
            return

        data = await state.get_data()
        article_id = data.get("article_id")
        if not article_id:
            await state.clear()
            await message.answer(
                "Session expired. Use Check Pending again.",
                reply_markup=admin_main_menu(),
            )
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await state.clear()
            await message.answer(
                "Article not found.",
                reply_markup=admin_main_menu(),
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await state.clear()
            await message.answer(
                "This article is no longer pending.",
                reply_markup=admin_main_menu(),
            )
            return

        current = (article.telegram_text or "").strip()
        article.telegram_text = f"{current}\n\n{url}".strip() if current else url
        await sync_to_async(article.save)(update_fields=["telegram_text"])
        await _update_preview_after_change(message, state, article)
        await state.clear()
        await message.answer(
            f"✅ Link appended to article #{article_id}.",
            reply_markup=admin_main_menu(),
        )

    return router
