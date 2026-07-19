"""Fetch and display pending articles as a paginated digest list."""

from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter, is_admin_user
from core.bot.config import BotConfig
from core.bot.keyboards import (
    ACTION_DIGEST_PAGE,
    ACTION_OPEN_ARTICLE,
    CHECK_PENDING_BUTTON,
    CheckPendingButtonFilter,
    digest_keyboard,
    main_menu,
    normalize_button_text,
)
from core.bot.services import (
    DIGEST_PAGE_SIZE,
    format_digest_message,
    load_article_for_bot,
    load_pending_digest_page,
    send_article_review_card,
)
from core.models import NewsArticle

logger = logging.getLogger(__name__)

_bot_username_cache: str | None = None


async def _resolve_bot_username(bot) -> str | None:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = await bot.get_me()
        _bot_username_cache = me.username
    except Exception as exc:
        logger.warning("Could not resolve bot username for digest deep links: %r", exc)
        _bot_username_cache = None
    return _bot_username_cache


async def _send_or_edit_digest(
    *,
    bot,
    chat_id: int,
    text: str,
    markup,
    edit_message: Message | None = None,
) -> None:
    """Send/edit digest HTML; fall back to plain text if Telegram rejects entities."""
    kwargs = {
        "parse_mode": "HTML",
        "reply_markup": markup,
        "disable_web_page_preview": True,
    }
    try:
        if edit_message is not None:
            await edit_message.edit_text(text, **kwargs)
        else:
            await bot.send_message(chat_id, text, **kwargs)
        return
    except TelegramBadRequest as exc:
        logger.warning("Digest HTML rejected by Telegram (%s); retrying plain text.", exc)

    # Strip tags for a safe plain-text fallback (keep readable content).
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html.unescape(plain)
    plain_kwargs = {
        "reply_markup": markup,
        "disable_web_page_preview": True,
    }
    if edit_message is not None:
        try:
            await edit_message.edit_text(plain, **plain_kwargs)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, plain, **plain_kwargs)


async def _render_digest_page(
    *,
    bot,
    chat_id: int,
    page: int,
    edit_message: Message | None = None,
) -> None:
    articles, total = await sync_to_async(load_pending_digest_page)(
        page,
        page_size=DIGEST_PAGE_SIZE,
    )

    if total == 0:
        text = "هیچ خبری در ۲۴ ساعت اخیر در انتظار تایید نیست."
        if edit_message is not None:
            try:
                await edit_message.edit_text(text, reply_markup=None)
            except Exception:
                await bot.send_message(chat_id, text)
        else:
            await bot.send_message(chat_id, text)
        return

    # If the requested page is past the end (e.g. after rejects), clamp.
    max_page = max(0, (total - 1) // DIGEST_PAGE_SIZE)
    if page > max_page:
        page = max_page
        articles, total = await sync_to_async(load_pending_digest_page)(
            page,
            page_size=DIGEST_PAGE_SIZE,
        )

    bot_username = await _resolve_bot_username(bot)
    text = format_digest_message(
        articles,
        page=page,
        bot_username=bot_username,
        page_size=DIGEST_PAGE_SIZE,
    )
    markup = digest_keyboard(
        articles,
        page=page,
        total=total,
        page_size=DIGEST_PAGE_SIZE,
    )

    await _send_or_edit_digest(
        bot=bot,
        chat_id=chat_id,
        text=text,
        markup=markup,
        edit_message=edit_message,
    )


async def _send_pending_digest(message: Message) -> None:
    await _render_digest_page(
        bot=message.bot,
        chat_id=message.chat.id,
        page=0,
    )


async def _open_article_detail(bot, chat_id: int, article_id: int) -> str | None:
    """Send full review card. Returns an optional alert string for callback.answer."""
    try:
        article = await sync_to_async(load_article_for_bot)(article_id)
    except NewsArticle.DoesNotExist:
        return "خبر پیدا نشد."

    if article.status == NewsArticle.Status.REJECTED:
        return "این خبر رد شده است."

    await send_article_review_card(bot, chat_id, article)
    return None


async def _handle_check_pending(
    message: Message,
    state: FSMContext,
    config: BotConfig,
) -> None:
    """Show the digest list for admin review — never runs ingestion."""
    await state.clear()

    if normalize_button_text(message.text) != normalize_button_text(CHECK_PENDING_BUTTON):
        await message.answer(
            "⌨️ منوی پایین به‌روزرسانی شد.",
            reply_markup=main_menu(),
        )

    if not is_admin_user(message.from_user, config.allowed_admin_ids):
        await message.answer(
            "📌 فقط  ادمین ها دسترسی دارند به این دکمه",
            reply_markup=main_menu(),
        )
        return

    await _send_pending_digest(message)


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="check_pending")

    @router.message(Command("check_pending"), admin_filter)
    async def cmd_check_pending(message: Message, state: FSMContext) -> None:
        await state.clear()
        await _send_pending_digest(message)

    @router.message(CheckPendingButtonFilter())
    async def btn_check_pending(message: Message, state: FSMContext) -> None:
        await _handle_check_pending(message, state, config)

    @router.callback_query(F.data.startswith(f"{ACTION_DIGEST_PAGE}:"))
    async def on_digest_page(callback: CallbackQuery) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        if callback.message is None or not callback.data:
            await callback.answer("داده نامعتبر.")
            return

        try:
            page = int(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer("صفحه نامعتبر.")
            return

        page = max(0, page)
        await _render_digest_page(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            page=page,
            edit_message=callback.message,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{ACTION_OPEN_ARTICLE}:"))
    async def on_open_article(callback: CallbackQuery) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        if callback.message is None or not callback.data:
            await callback.answer("داده نامعتبر.")
            return

        try:
            article_id = int(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer("داده نامعتبر.")
            return

        alert = await _open_article_detail(
            callback.bot,
            callback.message.chat.id,
            article_id,
        )
        if alert:
            await callback.answer(alert, show_alert=True)
            return
        await callback.answer()

    return router
