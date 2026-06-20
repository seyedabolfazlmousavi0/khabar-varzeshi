"""Editorial Telegram bot for approving/rejecting pending NewsArticle rows.

Run with:

    python manage.py run_bot

Uses aiogram 3 with FSM for inline edit/link workflows, a persistent admin
reply keyboard, and centralized ALLOWED_ADMIN_IDS authorization.
"""

from __future__ import annotations

# --- Force IPv4 for ALL outbound HTTP traffic ---------------------------------
# Must run BEFORE any HTTP client (aiohttp, urllib3, httpx, etc.) is imported.
import socket

import urllib3.util.connection as urllib3_cn

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo
# -----------------------------------------------------------------------------

import logging
import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.bot.app import run_bot
from core.bot.config import load_bot_config

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the editorial Telegram bot (aiogram long polling). "
        "Admins use the Check Pending button or /check_pending to review articles."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            config = load_bot_config()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.HTTP_INFO(
                f"Database: {settings.DATABASES['default']['NAME']}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Bot is up. Allowed admin IDs: "
                f"{', '.join(str(uid) for uid in sorted(config.allowed_admin_ids))}. "
                "Press Ctrl+C to stop."
            )
        )

        try:
            while True:
                try:
                    run_bot()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Network error: {exc}. Restarting bot in 10 seconds..."
                        )
                    )
                    time.sleep(10)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nBot stopped by user."))
