"""Reply and inline keyboards for the editorial bot."""

from __future__ import annotations

from aiogram.types import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

CHECK_PENDING_BUTTON = "📋 بررسی آخرین اخبار"

BTN_APPROVE = "✅ تایید و ارسال"
BTN_REJECT = "❌ رد کردن"
BTN_EDIT = "✏️ ویرایش متن"
BTN_ADD_LINK = "🔗 افزودن لینک"

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_EDIT = "edit"
ACTION_ADD_LINK = "addlink"


def admin_main_menu() -> ReplyKeyboardMarkup:
    """Persistent bottom menu visible only to authorized admins."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CHECK_PENDING_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    """Remove the custom reply keyboard for non-admin users."""
    return ReplyKeyboardRemove()


def edit_force_reply() -> ForceReply:
    """Force the admin to reply with edited post body text."""
    return ForceReply(force_reply=True, selective=True)


def article_review_keyboard(article_id: int) -> InlineKeyboardMarkup:
    """Inline actions shown while reviewing a pending article."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_APPROVE,
                    callback_data=f"{ACTION_APPROVE}:{article_id}",
                ),
                InlineKeyboardButton(
                    text=BTN_REJECT,
                    callback_data=f"{ACTION_REJECT}:{article_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=BTN_EDIT,
                    callback_data=f"{ACTION_EDIT}:{article_id}",
                ),
                InlineKeyboardButton(
                    text=BTN_ADD_LINK,
                    callback_data=f"{ACTION_ADD_LINK}:{article_id}",
                ),
            ],
        ]
    )
