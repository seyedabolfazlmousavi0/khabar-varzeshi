"""Editorial Telegram bot for approving/rejecting pending NewsArticle rows.

Run with:

    python manage.py run_bot

Uses aiogram 3 with FSM for inline edit/link workflows, a persistent admin
reply keyboard, and centralized ALLOWED_ADMIN_IDS authorization.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Any

import urllib3.util.connection as urllib3_cn

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.bot.app import run_bot
from core.bot.config import load_bot_config

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

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
