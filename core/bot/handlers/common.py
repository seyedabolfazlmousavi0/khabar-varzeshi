"""Start, help, and cancel handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter, is_admin_user
from core.bot.config import BotConfig
from core.bot.keyboards import CHECK_PENDING_BUTTON, main_menu
from core.bot.services import (
    NEWS_DEEP_LINK_PREFIX,
    load_article_for_bot,
    refresh_article_preview,
    send_article_review_card,
)
from core.bot.site_publish import review_keyboard_for_article
from core.models import NewsArticle


async def _open_news_from_start(
    message: Message,
    config: BotConfig,
    raw_args: str,
) -> bool:
    """Handle ``/start news_<id>`` deep links from digest titles. Return True if handled."""
    args = (raw_args or "").strip()
    if not args.startswith(NEWS_DEEP_LINK_PREFIX):
        return False

    if not is_admin_user(message.from_user, config.allowed_admin_ids):
        await message.answer(
            "📌 فقط ادمین‌ها به جزئیات خبر دسترسی دارند.",
            reply_markup=main_menu(),
        )
        return True

    try:
        article_id = int(args[len(NEWS_DEEP_LINK_PREFIX) :])
    except ValueError:
        await message.answer("شناسه خبر نامعتبر است.", reply_markup=main_menu())
        return True

    try:
        article = await sync_to_async(load_article_for_bot)(article_id)
    except NewsArticle.DoesNotExist:
        await message.answer("خبر پیدا نشد.", reply_markup=main_menu())
        return True

    if article.status == NewsArticle.Status.REJECTED:
        await message.answer("این خبر رد شده است.", reply_markup=main_menu())
        return True

    await send_article_review_card(message.bot, message.chat.id, article)
    return True


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="common")

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message, command: CommandObject) -> None:
        if await _open_news_from_start(message, config, command.args or ""):
            return

        if message.from_user and message.from_user.id in config.allowed_admin_ids:
            await message.answer(
                "سلام! من ربات سردبیر خبرورزشی هستم.\n\n"
                f"برای دریافت آخرین اخبار، دکمه <b>{CHECK_PENDING_BUTTON}</b> "
                "یا دستور /check_pending را بزنید.\n"
                "در لیست خلاصه، روی تیتر هر خبر بزنید تا جزئیات و دکمه‌های انتشار باز شود.",
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
