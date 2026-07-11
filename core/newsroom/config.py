"""Newsroom dashboard credentials and Selenium settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from dotenv import load_dotenv


@dataclass(frozen=True)
class NewsroomConfig:
    login_url: str
    create_url: str
    username: str
    password: str
    wait_timeout: int
    login_wait_timeout: int
    ckeditor_wait_timeout: int
    micro_delay: float
    headless: bool


def load_newsroom_config() -> NewsroomConfig:
    load_dotenv(settings.BASE_DIR / ".env")

    username = os.getenv("NEWSROOM_USERNAME", "").strip()
    password = os.getenv("NEWSROOM_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError(
            "NEWSROOM_USERNAME and NEWSROOM_PASSWORD must be set in .env "
            "for site publishing."
        )

    return NewsroomConfig(
        login_url=os.getenv(
            "NEWSROOM_LOGIN_URL",
            "https://newsroom.khabarvarzeshi.com/login/login.xhtml",
        ),
        create_url=os.getenv(
            "NEWSROOM_CREATE_URL",
            "https://newsroom.khabarvarzeshi.com/news.xhtml",
        ),
        username=username,
        password=password,
        wait_timeout=int(os.getenv("SELENIUM_WAIT_TIMEOUT", "25")),
        login_wait_timeout=int(os.getenv("SELENIUM_LOGIN_WAIT_TIMEOUT", "30")),
        ckeditor_wait_timeout=int(os.getenv("SELENIUM_CKEDITOR_WAIT_TIMEOUT", "45")),
        micro_delay=float(os.getenv("SELENIUM_MICRO_DELAY", "0.5")),
        headless=os.getenv("NEWSROOM_HEADLESS", "1").strip().lower()
        in {"1", "true", "yes", "on"},
    )
