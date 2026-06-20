"""Reply and inline keyboards for the editorial bot."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

CHECK_PENDING_BUTTON = "📋 Check Pending"

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


def article_review_keyboard(article_id: int) -> InlineKeyboardMarkup:
    """Inline actions shown while reviewing a pending article."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve & Publish",
                    callback_data=f"{ACTION_APPROVE}:{article_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"{ACTION_REJECT}:{article_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Edit",
                    callback_data=f"{ACTION_EDIT}:{article_id}",
                ),
                InlineKeyboardButton(
                    text="🔗 Add Link",
                    callback_data=f"{ACTION_ADD_LINK}:{article_id}",
                ),
            ],
        ]
    )
