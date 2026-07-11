"""Start, help, and cancel handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter
from core.bot.config import BotConfig
from core.bot.keyboards import CHECK_PENDING_BUTTON, main_menu
from core.bot.site_publish import review_keyboard_for_article
from core.bot.services import load_article_for_bot, refresh_article_preview


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="common")

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        if message.from_user and message.from_user.id in config.allowed_admin_ids:
            await message.answer(
                "سلام! من ربات سردبیر خبرورزشی هستم.\n\n"
                f"برای دریافت آخرین اخبار، دکمه <b>{CHECK_PENDING_BUTTON}</b> "
                "یا دستور /check_pending را بزنید.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
            return

        await message.answer(
            "سلام! به ربات خبرورزشی خوش آمدید.",
            reply_markup=main_menu(),
        )

    @router.message(Command("cancel"), admin_filter)
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        if current is None:
            await message.answer(
                "عملیاتی برای لغو وجود ندارد.",
                reply_markup=main_menu(),
            )
            return

        data = await state.get_data()
        article_id = data.get("article_id")
        preview_chat_id = data.get("preview_chat_id")
        preview_message_id = data.get("preview_message_id")

        preview_is_photo = data.get("preview_is_photo", False)

        await state.clear()

        if article_id and preview_chat_id and preview_message_id:
            try:
                article = await sync_to_async(load_article_for_bot)(article_id)
                await refresh_article_preview(
                    message.bot,
                    chat_id=preview_chat_id,
                    message_id=preview_message_id,
                    article=article,
                    reply_markup=review_keyboard_for_article(article),
                    is_photo=bool(preview_is_photo),
                )
            except Exception:
                pass

        await message.answer(
            "عملیات لغو شد.",
            reply_markup=main_menu(),
        )

    return router
