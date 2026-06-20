"""Fetch and display pending articles for admin review."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import CHECK_PENDING_BUTTON, article_review_keyboard
from core.bot.services import (
    format_article_message,
    load_pending_articles,
    resolve_image_url,
    send_with_optional_image,
)

logger = logging.getLogger(__name__)


async def _send_pending_batch(message: Message, config: BotConfig) -> None:
    pending = await sync_to_async(load_pending_articles)(config.pending_batch_size)

    if not pending:
        await message.answer("هیچ خبر در انتظار تاییدی وجود ندارد.")
        return

    await message.answer(
        f"📋 <b>{len(pending)}</b> خبر در انتظار تایید:",
        parse_mode="HTML",
    )

    for article in pending:
        image_url = await sync_to_async(resolve_image_url)(article)
        preview_text = await sync_to_async(format_article_message)(article)
        try:
            await send_with_optional_image(
                message.bot,
                message.chat.id,
                preview_text,
                image_url,
                reply_markup=article_review_keyboard(article.id),
            )
        except Exception as exc:
            logger.warning("Failed to send article %s: %r", article.id, exc)


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="check_pending")

    @router.message(Command("check_pending"), admin_filter)
    async def cmd_check_pending(message: Message, state: FSMContext) -> None:
        await state.clear()
        await _send_pending_batch(message, config)

    @router.message(F.text == CHECK_PENDING_BUTTON, admin_filter)
    async def btn_check_pending(message: Message, state: FSMContext) -> None:
        await state.clear()
        await _send_pending_batch(message, config)

    return router
