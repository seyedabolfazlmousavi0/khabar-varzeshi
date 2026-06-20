"""Inline callback handlers for approving, rejecting, and starting FSM edits."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from core.bot.auth import AdminFilter, is_admin_user
from core.bot.config import BotConfig
from core.bot.keyboards import (
    ACTION_ADD_LINK,
    ACTION_APPROVE,
    ACTION_EDIT,
    ACTION_REJECT,
)
from core.bot.services import (
    finalize_review_message,
    load_article_for_bot,
    resolve_image_url,
    send_with_optional_image,
)
from core.bot.states import AddLinkStates, EditNewsStates
from core.models import NewsArticle

logger = logging.getLogger(__name__)


def _parse_article_id(callback_data: str | None) -> int | None:
    if not callback_data or ":" not in callback_data:
        return None
    _, _, raw_id = callback_data.partition(":")
    try:
        return int(raw_id)
    except ValueError:
        return None


def build_router(config: BotConfig, admin_filter: AdminFilter) -> Router:
    router = Router(name="review")

    @router.callback_query(F.data.startswith(f"{ACTION_APPROVE}:"))
    async def on_approve(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("Access denied.", show_alert=True)
            return

        await state.clear()

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("Invalid data.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("Article not found.")
            await finalize_review_message(
                callback.bot,
                callback.message,
                suffix="⚠️ This article no longer exists.",
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("This article is no longer pending.")
            return

        image_url = await sync_to_async(resolve_image_url)(article)
        try:
            await send_with_optional_image(
                callback.bot,
                config.public_channel_id,
                article.telegram_text or "",
                image_url,
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish article %s to public channel: %r",
                article.id,
                exc,
            )
            await callback.answer("Failed to publish to channel.", show_alert=True)
            await callback.message.answer(f"❌ Channel publish error: {exc}")
            return

        article.status = NewsArticle.Status.PUBLISHED
        await sync_to_async(article.save)(update_fields=["status"])
        await callback.answer("✅ Published.")
        await finalize_review_message(
            callback.bot,
            callback.message,
            suffix="✅ <b>Approved and published.</b>",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_REJECT}:"))
    async def on_reject(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("Access denied.", show_alert=True)
            return

        await state.clear()

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("Invalid data.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("Article not found.")
            await finalize_review_message(
                callback.bot,
                callback.message,
                suffix="⚠️ This article no longer exists.",
            )
            return

        article.status = NewsArticle.Status.REJECTED
        await sync_to_async(article.save)(update_fields=["status"])
        await callback.answer("❌ Rejected.")
        await finalize_review_message(
            callback.bot,
            callback.message,
            suffix="❌ <b>Rejected by editor.</b>",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_EDIT}:"))
    async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("Access denied.", show_alert=True)
            return

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("Invalid data.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("Article not found.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("This article is no longer pending.")
            return

        await state.set_state(EditNewsStates.waiting_for_text)
        await state.update_data(
            article_id=article_id,
            preview_chat_id=callback.message.chat.id,
            preview_message_id=callback.message.message_id,
        )
        await callback.answer()
        await callback.message.answer(
            f"✏️ Send the new Telegram text for article <b>#{article_id}</b>.\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_ADD_LINK}:"))
    async def on_add_link(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("Access denied.", show_alert=True)
            return

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("Invalid data.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("Article not found.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("This article is no longer pending.")
            return

        await state.set_state(AddLinkStates.waiting_for_url)
        await state.update_data(
            article_id=article_id,
            preview_chat_id=callback.message.chat.id,
            preview_message_id=callback.message.message_id,
        )
        await callback.answer()
        await callback.message.answer(
            f"🔗 Send a URL to append to article <b>#{article_id}</b>.\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
        )

    return router
