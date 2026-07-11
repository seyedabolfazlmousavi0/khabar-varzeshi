"""
Standalone Selenium script for submitting news on Khabar Varzeshi Newsroom.

This file is kept for manual testing. Production publishing is handled by
``core.newsroom`` and triggered from the Telegram bot.

Run:
    python test_automation.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "khabar_varzeshi.settings")
django.setup()

from core.newsroom.config import load_newsroom_config
from core.newsroom.exceptions import SitePublishError
from core.newsroom.publisher import ArticlePublishPayload, publish_article_to_site

SAMPLE_HEADLINE = (
    "جام جهانی ۲۰۲۶؛ بازیکنان باشگاه‌های لیگ برتر چند گل به ثمر رسانده‌اند؟"
)
SAMPLE_LEAD = (
    "رقابت داغ ستارگان فوتبال جهان در جام جهانی ۲۰۲۶، جدول گلزنی باشگاه‌ها را "
    "با شگفتی‌های غیرمنتظره‌ای روبه‌رو کرده است؛ آمارها از پیشتازی مدعیان "
    "اسپانیایی و فرانسوی و عملکرد غافلگیرکننده برخی تیم‌های انگلیسی حکایت دارند."
)
SAMPLE_BODY = (
    "<h2>تحلیل آمارهای شگفت‌انگیز جام جهانی ۲۰۲۶؛ رقابت داغ غول‌های اروپایی</h2>"
    "<p>بررسی جدیدترین آمارهای منتشر شده از <strong>جام جهانی ۲۰۲۶</strong> نشان "
    "می‌دهد که رقابت میان باشگاه‌های بزرگ اروپایی برای تصاحب عنوان گلزن‌ترین تیم "
    "تورنمنت به اوج خود رسیده است.</p>"
)
SAMPLE_IMAGE_PATH = os.getenv(
    "NEWSROOM_TEST_IMAGE",
    str(Path(__file__).resolve().parent / "test_image.jpg"),
)


def main() -> int:
    image_path = SAMPLE_IMAGE_PATH if Path(SAMPLE_IMAGE_PATH).is_file() else None
    payload = ArticlePublishPayload(
        headline=SAMPLE_HEADLINE,
        lead=SAMPLE_LEAD,
        body=SAMPLE_BODY,
        image_path=image_path,
        image_caption=SAMPLE_HEADLINE,
    )

    try:
        config = load_newsroom_config()
        # Manual test runs with a visible browser unless NEWSROOM_HEADLESS=1.
        if os.getenv("NEWSROOM_HEADLESS") is None:
            from dataclasses import replace

            config = replace(config, headless=False)

        publish_article_to_site(payload, config=config)
        print("[STEP] Completed: All Steps", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", flush=True)
        return 130
    except SitePublishError as exc:
        print(f"\n[FAILED] {exc}\n", flush=True)
        return 1
    except Exception as exc:
        print(f"\n[FAILED] Unexpected error: {exc}\n", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
