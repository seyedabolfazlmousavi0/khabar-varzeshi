"""Inline callback handlers for approving, rejecting, and starting FSM edits."""

from __future__ import annotations

import asyncio
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
    ACTION_PUBLISH_SITE,
    ACTION_REJECT,
    article_review_keyboard,
    edit_force_reply,
)
from core.bot.services import (
    finalize_review_message,
    format_article_message,
    load_article_for_bot,
    resolve_image_url,
    send_with_optional_image,
    update_review_message,
)
from core.bot.site_publish import is_site_publish_in_progress, run_site_publish_job
from core.bot.states import AddLinkStates, EditNewsStates
from core.bot.text_compose import (
    SITE_LINK_ANCHOR,
    get_editable_body,
    normalize_telegram_text,
    parse_telegram_text,
)
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
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        await state.clear()

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("داده نامعتبر.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            await finalize_review_message(
                callback.bot,
                callback.message,
                suffix="⚠️ این خبر دیگر در پایگاه داده وجود ندارد.",
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            return

        publish_text = normalize_telegram_text(article.telegram_text)
        image_url = await sync_to_async(resolve_image_url)(article)
        try:
            await send_with_optional_image(
                callback.bot,
                config.public_channel_id,
                publish_text,
                image_url,
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish article %s to public channel: %r",
                article.id,
                exc,
            )
            await callback.answer("ارسال به کانال ناموفق بود.", show_alert=True)
            await callback.message.answer(f"❌ خطا در ارسال به کانال: {exc}")
            return

        article.status = NewsArticle.Status.PUBLISHED
        await sync_to_async(article.save)(update_fields=["status"])
        await callback.answer("✅ منتشر شد.")
        await finalize_review_message(
            callback.bot,
            callback.message,
            suffix="✅ <b>تایید و ارسال شد.</b>",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_REJECT}:"))
    async def on_reject(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        await state.clear()

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("داده نامعتبر.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            await finalize_review_message(
                callback.bot,
                callback.message,
                suffix="⚠️ این خبر دیگر در پایگاه داده وجود ندارد.",
            )
            return

        article.status = NewsArticle.Status.REJECTED
        await sync_to_async(article.save)(update_fields=["status"])
        await callback.answer("❌ رد شد.")
        await finalize_review_message(
            callback.bot,
            callback.message,
            suffix="❌ <b>رد شد.</b>",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_EDIT}:"))
    async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("داده نامعتبر.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            return

        parsed = parse_telegram_text(article.telegram_text)
        editable_body = get_editable_body(article.telegram_text)
        preview_text = await sync_to_async(format_article_message)(article)

        await update_review_message(
            callback.bot,
            callback.message,
            preview_text,
            reply_markup=None,
            suffix="✏️ <b>در حال ویرایش...</b>",
        )

        await state.set_state(EditNewsStates.waiting_for_text)
        await state.update_data(
            article_id=article_id,
            preview_chat_id=callback.message.chat.id,
            preview_message_id=callback.message.message_id,
            preview_is_photo=bool(callback.message.photo),
            editable_body=editable_body,
            link_url=parsed.link_url,
            footer=parsed.footer,
            original_telegram_text=article.telegram_text or "",
        )
        await callback.answer()

        prompt_header = (
            f"✏️ متن فعلی پست (خبر #{article_id}):\n\n"
            "متن زیر را ویرایش کرده و در پاسخ ارسال کنید.\n"
            "برای لغو: /cancel"
        )
        if editable_body:
            await callback.message.answer(prompt_header)
            await callback.message.answer(
                editable_body,
                reply_markup=edit_force_reply(),
            )
        else:
            await callback.message.answer(
                f"{prompt_header}\n\n(متن فعلی خالی است — متن جدید را ارسال کنید.)",
                reply_markup=edit_force_reply(),
            )

    @router.callback_query(F.data.startswith(f"{ACTION_ADD_LINK}:"))
    async def on_add_link(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("داده نامعتبر.")
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            return

        parsed = parse_telegram_text(article.telegram_text)

        await state.set_state(AddLinkStates.waiting_for_url)
        await state.update_data(
            article_id=article_id,
            preview_chat_id=callback.message.chat.id,
            preview_message_id=callback.message.message_id,
            footer=parsed.footer,
        )
        await callback.answer()
        await callback.message.answer(
            f"🔗 لینک مقاله را برای خبر <b>#{article_id}</b> ارسال کنید.\n\n"
            f"متن لینک ثابت خواهد بود: «{SITE_LINK_ANCHOR}»\n"
            "برای لغو: /cancel",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_PUBLISH_SITE}:"))
    async def on_publish_site(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        await state.clear()

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None or callback.from_user is None:
            await callback.answer("داده نامعتبر.")
            return

        if is_site_publish_in_progress(article_id):
            await callback.answer("انتشار این خبر در حال انجام است.", show_alert=True)
            return

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            await finalize_review_message(
                callback.bot,
                callback.message,
                suffix="⚠️ این خبر دیگر در پایگاه داده وجود ندارد.",
            )
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            return

        missing_fields: list[str] = []
        if not (article.site_title or "").strip():
            missing_fields.append("تیتر سایت")
        if not (article.site_lead or "").strip():
            missing_fields.append("لید سایت")
        if not (article.site_body or "").strip():
            missing_fields.append("متن سایت")

        if missing_fields:
            await callback.answer(
                "فیلدهای سایت ناقص است: " + "، ".join(missing_fields),
                show_alert=True,
            )
            return

        await callback.answer("انتشار در پس‌زمینه آغاز شد.")

        preview_text = await sync_to_async(format_article_message)(article)
        await update_review_message(
            callback.bot,
            callback.message,
            preview_text,
            reply_markup=article_review_keyboard(article_id, publishing=True),
            suffix="⏳ <b>در حال انتشار روی سایت...</b>",
        )

        asyncio.create_task(
            run_site_publish_job(
                bot=callback.bot,
                article_id=article_id,
                operator_chat_id=callback.from_user.id,
                review_chat_id=callback.message.chat.id,
                review_message_id=callback.message.message_id,
                review_is_photo=bool(callback.message.photo),
            )
        )

    return router
