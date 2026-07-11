"""Reply and inline keyboards for the editorial bot."""

from __future__ import annotations

import unicodedata

from aiogram.filters import BaseFilter
from aiogram.types import (
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# Single source of truth for the reply-keyboard label and text-router matching.
CHECK_PENDING_BUTTON = "بررسی آخرین اخبار 📋"

# Telegram clients may cache an older reply keyboard until the user sends /start.
_CHECK_PENDING_ALIASES = frozenset(
    {
        CHECK_PENDING_BUTTON,
        "📋 بررسی آخرین اخبار",
        "📋 Check Pending",
    }
)


def normalize_button_text(text: str | None) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFC", text.strip())


def is_check_pending_button(text: str | None) -> bool:
    """True when ``text`` matches the check-pending reply keyboard label."""
    normalized = normalize_button_text(text)
    return normalized in {normalize_button_text(label) for label in _CHECK_PENDING_ALIASES}


class CheckPendingButtonFilter(BaseFilter):
    """Match incoming messages sent via the check-pending reply keyboard button."""

    async def __call__(self, message: Message) -> bool:
        return is_check_pending_button(message.text)


BTN_APPROVE = "✅ تایید و ارسال"
BTN_APPROVE_DONE = "✅ ارسال شد به کانال"
BTN_REJECT = "❌ رد کردن"
BTN_EDIT = "✏️ ویرایش متن"
BTN_ADD_LINK = "🔗 افزودن لینک"
BTN_PUBLISH_SITE = "انتشار خبر در سایت"
BTN_PUBLISH_SITE_IN_PROGRESS = "⏳ در حال انتشار..."
BTN_PUBLISH_SITE_DONE = "✅ منتشر شد در سایت"

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_EDIT = "edit"
ACTION_ADD_LINK = "addlink"
ACTION_PUBLISH_SITE = "publish_site"


def main_menu() -> ReplyKeyboardMarkup:
    """Persistent bottom menu visible to all users."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CHECK_PENDING_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_main_menu() -> ReplyKeyboardMarkup:
    """Alias for :func:`main_menu` (kept for existing call sites)."""
    return main_menu()


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    """Remove the custom reply keyboard for non-admin users."""
    return ReplyKeyboardRemove()


def edit_force_reply() -> ForceReply:
    """Force the admin to reply with edited post body text."""
    return ForceReply(force_reply=True, selective=True)


def article_review_keyboard(
    article_id: int,
    *,
    publishing: bool = False,
    channel_published: bool = False,
    site_published: bool = False,
) -> InlineKeyboardMarkup:
    """Inline actions shown while reviewing a pending article."""
    if channel_published:
        approve_text = BTN_APPROVE_DONE
    else:
        approve_text = BTN_APPROVE

    if site_published:
        publish_text = BTN_PUBLISH_SITE_DONE
    elif publishing:
        publish_text = BTN_PUBLISH_SITE_IN_PROGRESS
    else:
        publish_text = BTN_PUBLISH_SITE

    publish_button = InlineKeyboardButton(
        text=publish_text,
        callback_data=f"{ACTION_PUBLISH_SITE}:{article_id}",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=approve_text,
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
            [publish_button],
        ]
    )
