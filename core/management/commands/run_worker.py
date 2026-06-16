"""Periodic worker: every 2 minutes, run `fetch_news` and notify the admin
Telegram chat about any newly extracted articles.

Run with:

    python manage.py run_worker
"""

from __future__ import annotations

# --- Force IPv4 for ALL outbound HTTP traffic ---------------------------------
# Must run BEFORE any HTTP client (urllib3, httpx, etc.) is initialized.
# See fetch_news.py for the same patch and rationale.
import socket

import urllib3.util.connection as urllib3_cn

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo
# -----------------------------------------------------------------------------

import logging
import os
import time
from typing import Any

import schedule
import telebot
from telebot.apihelper import ApiTelegramException

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from dotenv import load_dotenv

from core.models import NewsArticle


logger = logging.getLogger(__name__)

CHECK_INTERVAL_MINUTES = 2


class Command(BaseCommand):
    help = (
        "Run the periodic worker. Every "
        f"{CHECK_INTERVAL_MINUTES} minute(s) the worker calls `fetch_news` "
        "and, if new pending articles were added, notifies the admin "
        "Telegram chat."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        load_dotenv(settings.BASE_DIR / ".env")

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        admin_id_raw = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

        if not token or token == "your_bot_token_here":
            raise CommandError("TELEGRAM_BOT_TOKEN is not set in .env.")
        if not admin_id_raw or admin_id_raw == "your_chat_id_here":
            raise CommandError("TELEGRAM_ADMIN_CHAT_ID is not set in .env.")

        try:
            admin_chat_id = int(admin_id_raw)
        except ValueError as exc:
            raise CommandError(
                "TELEGRAM_ADMIN_CHAT_ID must be a numeric chat id."
            ) from exc

        bot = telebot.TeleBot(token, parse_mode="HTML")

        def job() -> None:
            try:
                close_old_connections()

                pending_before = NewsArticle.objects.filter(
                    status=NewsArticle.Status.PENDING
                ).count()

                self.stdout.write(
                    self.style.MIGRATE_HEADING(
                        f"\n[worker] cycle started "
                        f"(pending before: {pending_before})"
                    )
                )

                call_command("fetch_news")

                pending_after = NewsArticle.objects.filter(
                    status=NewsArticle.Status.PENDING
                ).count()

                new_count = pending_after - pending_before

                if new_count > 0:
                    text = (
                        f"🔔 <b>{new_count} خبر جدید استخراج و بازنویسی شد!</b>\n\n"
                        "برای بررسی و تایید، دستور /check_pending را ارسال کنید."
                    )
                    try:
                        bot.send_message(
                            admin_chat_id,
                            text,
                            parse_mode="HTML",
                        )
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"[worker] notified admin of "
                                f"{new_count} new article(s)."
                            )
                        )
                    except ApiTelegramException as exc:
                        logger.warning(
                            "[worker] Failed to notify admin: %r", exc
                        )
                        self.stderr.write(
                            self.style.WARNING(
                                f"[worker] failed to notify admin: {exc!r}"
                            )
                        )
                else:
                    self.stdout.write(
                        f"[worker] no new articles "
                        f"(pending now: {pending_after})."
                    )

            except Exception as exc:
                logger.exception("[worker] cycle crashed")
                self.stderr.write(
                    self.style.ERROR(
                        f"[worker] cycle crashed: {exc!r}. "
                        "Will retry next cycle."
                    )
                )

        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(job)

        self.stdout.write(
            self.style.SUCCESS(
                f"Worker started. Running `fetch_news` every "
                f"{CHECK_INTERVAL_MINUTES} minute(s). "
                f"Admin chat id: {admin_chat_id}. Press Ctrl+C to stop."
            )
        )

        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nWorker stopped by user."))
