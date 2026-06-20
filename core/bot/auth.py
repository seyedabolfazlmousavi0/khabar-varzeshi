"""Centralized authorization for administrative bot features."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject, User


def is_admin_user(user: User | None, allowed_admin_ids: frozenset[int]) -> bool:
    """Return True when ``user`` is in the allowed admin ID list."""
    return user is not None and user.id in allowed_admin_ids


def resolve_user(event: TelegramObject) -> User | None:
    """Extract the acting user from a message or callback event."""
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    return getattr(event, "from_user", None)


class AdminFilter(BaseFilter):
    """Pass only events from users listed in ``allowed_admin_ids``."""

    def __init__(self, allowed_admin_ids: frozenset[int]) -> None:
        self.allowed_admin_ids = allowed_admin_ids

    async def __call__(self, event: TelegramObject) -> bool | dict[str, Any]:
        user = resolve_user(event)
        return is_admin_user(user, self.allowed_admin_ids)
