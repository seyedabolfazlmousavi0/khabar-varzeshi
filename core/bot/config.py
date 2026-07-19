"""Telegram bot configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from dotenv import load_dotenv


def _parse_admin_ids() -> frozenset[int]:
    """Return allowed Telegram user IDs for admin features."""
    raw = os.getenv("ALLOWED_ADMIN_IDS", "").strip()
    if raw:
        ids: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return frozenset(ids)

    legacy = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if legacy and legacy != "your_chat_id_here":
        return frozenset({int(legacy)})
    return frozenset()


@dataclass(frozen=True)
class BotConfig:
    token: str
    allowed_admin_ids: frozenset[int]
    public_channel_id: str
    pending_batch_size: int = 10


def load_bot_config() -> BotConfig:
    load_dotenv(settings.BASE_DIR / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    public_channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID", "")
    admin_ids = _parse_admin_ids()

    if not token or token == "your_bot_token_here":
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env.")
    if not admin_ids:
        raise ValueError(
            "No admin IDs configured. Set ALLOWED_ADMIN_IDS "
            "(comma-separated Telegram user IDs) or TELEGRAM_ADMIN_CHAT_ID."
        )
    if not public_channel or public_channel == "@YourPublicChannel":
        raise ValueError(
            "TELEGRAM_PUBLIC_CHANNEL_ID is not set in .env. "
            "Add the @username or numeric id of the public channel."
        )

    return BotConfig(
        token=token,
        allowed_admin_ids=admin_ids,
        public_channel_id=public_channel,
    )
