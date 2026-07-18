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
    ACTION_VIEW_FULL_SITE,
    edit_force_reply,
)
from core.bot.review_panels import (
    register_review_panel,
    remove_review_panels,
    sync_review_panels,
)
from core.bot.services import (
    finalize_review_message,
    format_review_message,
    load_article_for_bot,
    resolve_image_url,
    send_full_site_version,
    send_with_optional_image,
    update_review_message,
)
from core.bot.site_publish import (
    is_site_publish_in_progress,
    is_site_published,
    review_keyboard_for_article,
    run_site_publish_job,
)
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


def _review_keyboard(article: NewsArticle, *, publishing: bool = False):
    return review_keyboard_for_article(article, publishing=publishing)


def _ensure_panel_registered(callback: CallbackQuery, article_id: int) -> None:
    """Register the callback message in case this admin opened it before sync existed."""
    if callback.message is None:
        return
    register_review_panel(
        article_id,
        callback.message.chat.id,
        callback.message.message_id,
        is_photo=bool(callback.message.photo),
    )


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

        _ensure_panel_registered(callback, article_id)

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

        if article.status == NewsArticle.Status.PUBLISHED:
            await callback.answer("این خبر قبلاً به کانال ارسال شده است.", show_alert=True)
            await sync_review_panels(
                callback.bot,
                article_id,
                reply_markup=_review_keyboard(article),
            )
            return

        if article.status == NewsArticle.Status.REJECTED:
            await callback.answer("این خبر رد شده است.", show_alert=True)
            await remove_review_panels(callback.bot, article_id)
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
        await sync_review_panels(
            callback.bot,
            article_id,
            reply_markup=_review_keyboard(article),
            suffix="✅ <b>تایید و ارسال شد به کانال.</b>",
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

        _ensure_panel_registered(callback, article_id)

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

        if article.status == NewsArticle.Status.PUBLISHED:
            await callback.answer(
                "این خبر قبلاً منتشر شده و قابل رد نیست.",
                show_alert=True,
            )
            await sync_review_panels(
                callback.bot,
                article_id,
                reply_markup=_review_keyboard(article),
            )
            return

        if article.status == NewsArticle.Status.REJECTED:
            await callback.answer("این خبر قبلاً رد شده است.")
            await remove_review_panels(callback.bot, article_id)
            return

        article.status = NewsArticle.Status.REJECTED
        await sync_to_async(article.save)(update_fields=["status"])
        await callback.answer("❌ رد شد.")
        # Delete the preview from every admin's chat (fallback: mark as rejected).
        await remove_review_panels(callback.bot, article_id)

    @router.callback_query(F.data.startswith(f"{ACTION_EDIT}:"))
    async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin_user(callback.from_user, config.allowed_admin_ids):
            await callback.answer("دسترسی مجاز نیست.", show_alert=True)
            return

        article_id = _parse_article_id(callback.data)
        if article_id is None or callback.message is None:
            await callback.answer("داده نامعتبر.")
            return

        _ensure_panel_registered(callback, article_id)

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            await sync_review_panels(
                callback.bot,
                article_id,
                reply_markup=_review_keyboard(article),
            )
            return

        parsed = parse_telegram_text(article.telegram_text)
        editable_body = get_editable_body(article.telegram_text)
        preview_text = await sync_to_async(format_review_message)(
            article,
            is_photo=bool(callback.message.photo),
        )

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

        _ensure_panel_registered(callback, article_id)

        try:
            article = await sync_to_async(load_article_for_bot)(article_id)
        except NewsArticle.DoesNotExist:
            await callback.answer("خبر پیدا نشد.")
            return

        if article.status != NewsArticle.Status.PENDING:
            await callback.answer("این خبر دیگر در انتظار تایید نیست.")
            await sync_review_panels(
                callback.bot,
                article_id,
                reply_markup=_review_keyboard(article),
            )
            return

        parsed = parse_telegram_text(article.telegram_text)

        await state.set_state(AddLinkStates.waiting_for_url)
        await state.update_data(
            article_id=article_id,
            preview_chat_id=callback.message.chat.id,
            preview_message_id=callback.message.message_id,
            preview_is_photo=bool(callback.message.photo),
            footer=parsed.footer,
        )
        await callback.answer()
        await callback.message.answer(
            f"🔗 لینک مقاله را برای خبر <b>#{article_id}</b> ارسال کنید.\n\n"
            f"متن لینک ثابت خواهد بود: «{SITE_LINK_ANCHOR}»\n"
            "برای لغو: /cancel",
            parse_mode="HTML",
        )

    @router.callback_query(F.data.startswith(f"{ACTION_VIEW_FULL_SITE}:"))
    async def on_view_full_site(callback: CallbackQuery) -> None:
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
            await callback.answer("خبر پیدا نشد.", show_alert=True)
            return

        if not (article.site_body or "").strip() and not (article.site_title or "").strip():
            await callback.answer("متن سایت برای این خبر موجود نیست.", show_alert=True)
            return

        await callback.answer()
        try:
            await send_full_site_version(
                callback.bot,
                callback.message.chat.id,
                article,
                reply_to_message_id=callback.message.message_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send full site version for article %s: %r",
                article_id,
                exc,
            )
            await callback.message.answer(
                f"❌ خطا در نمایش نسخه کامل سایت: {exc}",
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

        _ensure_panel_registered(callback, article_id)

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

        if is_site_published(article_id):
            await callback.answer("این خبر قبلاً روی سایت منتشر شده است.", show_alert=True)
            await sync_review_panels(
                callback.bot,
                article_id,
                reply_markup=_review_keyboard(article),
            )
            return

        if article.status == NewsArticle.Status.REJECTED:
            await callback.answer("این خبر رد شده است.")
            await remove_review_panels(callback.bot, article_id)
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

        await sync_review_panels(
            callback.bot,
            article_id,
            reply_markup=_review_keyboard(article, publishing=True),
            suffix="⏳ <b>در حال انتشار روی سایت...</b>",
            include_status_suffix=False,
        )

        asyncio.create_task(
            run_site_publish_job(
                bot=callback.bot,
                article_id=article_id,
            )
        )

    return router
