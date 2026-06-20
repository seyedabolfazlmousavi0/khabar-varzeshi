"""FSM message handlers for editing article text and appending links."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import CHECK_PENDING_BUTTON, admin_main_menu, article_review_keyboard
from core.bot.services import (
    is_valid_http_url,
    load_article_for_bot,
    refresh_article_preview,
)
from core.bot.states import AddLinkStates, EditNewsStates
from core.bot.text_compose import compose_telegram_text, inject_site_link, parse_telegram_text
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

    await refresh_article_preview(
        message.bot,
        chat_id=preview_chat_id,
        message_id=preview_message_id,
        article=article,
        reply_markup=article_review_keyboard(article.id),
    )


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="fsm")

    @router.message(EditNewsStates.waiting_for_text, admin_filter)
    async def receive_edited_text(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("لطفاً متن plain ارسال کنید. /cancel برای لغو.")
            return

        data = await state.get_data()
        article_id = data.get("article_id")
        if not article_id:
            await state.clear()
            await message.answer(
                f"نشست منقضی شد. دوباره {CHECK_PENDING_BUTTON} را بزنید.",
                reply_markup=admin_main_menu(),
            )
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await state.clear()
            await message.answer(
                "خبر پیدا نشد.",
                reply_markup=admin_main_menu(),
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await state.clear()
            await message.answer(
                "این خبر دیگر در انتظار تایید نیست.",
                reply_markup=admin_main_menu(),
            )
            return

        link_url = data.get("link_url")
        footer = data.get("footer") or parse_telegram_text(article.telegram_text).footer
        article.telegram_text = compose_telegram_text(
            message.text.strip(),
            link_url=link_url,
            footer=footer,
        )
        await sync_to_async(article.save)(update_fields=["telegram_text"])
        await _update_preview_after_change(message, state, article)
        await state.clear()
        await message.answer(
            f"✅ متن تلگرام خبر #{article_id} به‌روزرسانی شد.",
            reply_markup=admin_main_menu(),
        )

    @router.message(AddLinkStates.waiting_for_url, admin_filter)
    async def receive_link_url(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("لطفاً یک URL ارسال کنید. /cancel برای لغو.")
            return

        url = message.text.strip()
        if not is_valid_http_url(url):
            await message.answer(
                "URL نامعتبر است. لینکی با http:// یا https:// بفرستید، "
                "یا /cancel برای لغو.",
            )
            return

        data = await state.get_data()
        article_id = data.get("article_id")
        if not article_id:
            await state.clear()
            await message.answer(
                f"نشست منقضی شد. دوباره {CHECK_PENDING_BUTTON} را بزنید.",
                reply_markup=admin_main_menu(),
            )
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await state.clear()
            await message.answer(
                "خبر پیدا نشد.",
                reply_markup=admin_main_menu(),
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await state.clear()
            await message.answer(
                "این خبر دیگر در انتظار تایید نیست.",
                reply_markup=admin_main_menu(),
            )
            return

        article.telegram_text = inject_site_link(article.telegram_text, url)
        await sync_to_async(article.save)(update_fields=["telegram_text"])
        await _update_preview_after_change(message, state, article)
        await state.clear()
        await message.answer(
            f"✅ لینک به خبر #{article_id} اضافه شد.",
            reply_markup=admin_main_menu(),
        )

    return router
