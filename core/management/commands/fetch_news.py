"""Fetch active RSS feeds, rewrite each new article with Gemini, and persist
the result as a `pending` `NewsArticle`.

Run with:

    python manage.py fetch_news
"""

from __future__ import annotations

# --- Force IPv4 for ALL outbound HTTP traffic ---------------------------------
# Must run BEFORE any HTTP client (urllib3, httpx, etc.) is initialized.
#
# The first patch covers urllib3-based libraries (requests, telebot, ...).
# The second patch covers everything else, because google-genai 2.x is built
# on httpx, which does NOT use urllib3 — without the socket-level patch the
# Gemini call can still resolve to an IPv6 address that times out.
import socket

import urllib3.util.connection as urllib3_cn

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo
# -----------------------------------------------------------------------------

import json
import os
import re
import time
import traceback
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from dotenv import load_dotenv
from google import genai
from google.genai import types
from requests.exceptions import ConnectTimeout as RequestsConnectTimeout
from requests.exceptions import ReadTimeout as RequestsReadTimeout
from requests.exceptions import Timeout as RequestsTimeout

from core.article_scraper import _clean_html_to_text, scrape_article_html
from core.models import NewsArticle, RssSource
from core.url_utils import normalize_article_url


DEFAULT_GEMINI_MODEL = "models/gemini-2.5-flash-lite"
GEMINI_REQUEST_TIMEOUT = 120  # seconds
TELEGRAM_CHANNEL_ID = "@KhabarVarzeshi"

# Every timeout-shaped exception we may encounter from any HTTP library.
TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.TimeoutException,
    RequestsReadTimeout,
    RequestsConnectTimeout,
    RequestsTimeout,
    TimeoutError,
)

# Required JSON keys the Gemini response must contain.
REQUIRED_KEYS = ("site_title", "site_lead", "site_body", "telegram_text")

# Matches an opening ``` or ```json fence, and the closing ``` fence.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

PROMPT_TEMPLATE = """\
You are the Editor-in-Chief of a world-class international sports news agency.

Your responsibility is NOT to translate the article.
Your responsibility is to understand the facts, verify their logical relationships, and produce a completely new Persian news article that reads as if it were originally written by a professional Iranian sports journalist.

========================
EDITORIAL PRINCIPLES
========================

The writing must be:

- Professional
- Formal
- Objective
- Elegant
- Natural Persian
- SEO-friendly
- Suitable for publication without editing

Never write like AI.
Never translate sentence-by-sentence.
Rewrite everything from scratch.

========================
STRICT LANGUAGE RULES
========================

The output MUST be written almost entirely in Persian.

English words are forbidden except when absolutely unavoidable.

Rules:

- Convert ALL player names into Persian phonetic writing.
  Example:
  Lionel Messi → لیونل مسی
  Kylian Mbappe → کیلیان امباپه
  Thomas Tuchel → توماس توخل

- Convert ALL coach names into Persian.

- Convert ALL club names into Persian.
  Example:
  Manchester United → منچستر یونایتد
  Real Madrid → رئال مادرید

- Convert ALL competition names into Persian.
  Example:
  Champions League → لیگ قهرمانان اروپا

- Convert months, dates and numbers into Persian writing.

- Never write English abbreviations.

Examples:

❌ FIFA
✅ فیفا

❌ UEFA
✅ یوفا

❌ Premier League
✅ لیگ برتر انگلیس

Only keep English when it is an official trademark that has no accepted Persian equivalent.

========================
WRITING STYLE
========================

Write like a respected sports newsroom.

Avoid:

- Clickbait
- Exaggeration
- Emotional language
- Social media style
- Informal words
- Slang

Never use phrases such as:

- بمب نقل‌وانتقالات
- شوک بزرگ
- زلزله
- کولاک
- ترکاند
- آتش به پا کرد
- همه را حیرت‌زده کرد
- جنجالی
- انفجار خبری

Instead use calm professional language.

========================
SITE TITLE
========================

Write one SEO title.

Requirements:

- 10 to 18 words
- Professional
- Mention the main event
- Mention the important player/team if relevant
- No clickbait
- No quotation marks
- No colon at the beginning

========================
SITE LEAD
========================

Write 2 short paragraphs.

The lead must answer:

چه اتفاقی افتاده؟
برای چه کسی؟
کجا؟
چرا اهمیت دارد؟

Length:
50–90 words.

========================
SITE BODY
========================

Write a complete HTML article.

Rules:

Use ONLY:

<h2>
<p>

Structure:

Introduction

<h2>جزئیات خبر</h2>

Several paragraphs

<h2>اهمیت این اتفاق</h2>

Several paragraphs

<h2>جمع‌بندی</h2>

Final paragraph.

The article should naturally flow.

Do NOT repeat sentences.

Length:
500–900 Persian words.

========================
TELEGRAM POST
========================

Write a Telegram post suitable for publication in a professional Persian sports news channel.

This is NOT a summary of the website article.
It is a standalone Telegram news post.

Length:
Approximately 40–120 Persian words.

Structure:

• One strong professional headline.

• One short paragraph explaining the main news.

• If the news contains an important quote, include only the most important quotation.

• If the news is about a match, clearly show the final score in a readable format.

• If the news is about a transfer, injury, suspension, contract or official statement, emphasize the most important fact.

• End with exactly:
{channel_id}

Writing Rules:

- Write naturally like a real Telegram sports newsroom.
- Keep sentences short.
- Never copy the website article.
- Never translate sentence-by-sentence.
- Avoid unnecessary details.
- No hashtags.
- Maximum two emojis.
- No HTML.
- No markdown.
- No English except unavoidable official brand names.
- Write player names, coaches, clubs and competitions in Persian.
- Do not exaggerate.
- Do not use clickbait.
- Do not ask questions.
========================
JSON FORMAT
========================

Return ONLY valid JSON.

Do NOT use Markdown.

Do NOT add explanations.

Return EXACTLY these keys:

{
  "site_title":"",
  "site_lead":"",
  "site_body":"",
  "telegram_text":""
}

Escape every quotation mark correctly.

========================
SOURCE MATERIAL
========================

Original Title:
{title}

Source:
{source_name}

Raw Content:

{content}
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` (or plain ```) wrappers Gemini sometimes adds."""
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()



# Image URLs hosted by these CDNs occasionally don't load when fetched by
# Telegram's servers. We don't filter them out — Telegram will reject them
# with an ApiTelegramException and the bot will gracefully fall back to text.
_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|bmp)(?:\?.*)?$", re.IGNORECASE)


def _looks_like_image_url(url: str, mime: str = "") -> bool:
    if not url or not isinstance(url, str):
        return False
    if mime and mime.lower().startswith("image/"):
        return True
    return bool(_IMG_EXT_RE.search(url))


def _extract_image_candidates(
    entry: feedparser.FeedParserDict,
    raw_html: str,
) -> list[tuple[str, str]]:
    """Return [(source, url), ...] for every image URL we can find on the entry.

    Sources tried, in priority order, mirror the most common RSS conventions:

      1. media:thumbnail   (Yahoo Media RSS — `entry.media_thumbnail`)
      2. media:content     (Yahoo Media RSS — `entry.media_content`,
                            filtered to image/* types)
      3. enclosure         (RSS 2.0 standard — `entry.enclosures`,
                            filtered to image MIME types)
      4. <img src="..."/>  (first image in the HTML body)
      5. entry.image       (RSS 1.0 / Atom — sometimes `{href: ...}`)
      6. entry.itunes_image, entry.links rel=enclosure as a last resort
    """
    candidates: list[tuple[str, str]] = []

    thumbnails = getattr(entry, "media_thumbnail", None) or []
    for t in thumbnails:
        url = t.get("url") if isinstance(t, dict) else None
        if _looks_like_image_url(url or ""):
            candidates.append(("media:thumbnail", url))

    media_contents = getattr(entry, "media_content", None) or []
    for m in media_contents:
        if not isinstance(m, dict):
            continue
        url = m.get("url")
        mime = m.get("type", "") or m.get("medium", "")
        if _looks_like_image_url(url or "", mime):
            candidates.append(("media:content", url))

    enclosures = getattr(entry, "enclosures", None) or []
    for e in enclosures:
        if not isinstance(e, dict):
            continue
        url = e.get("url") or e.get("href")
        mime = e.get("type", "")
        if _looks_like_image_url(url or "", mime):
            candidates.append(("enclosure", url))

    if raw_html:
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"):
                candidates.append(("html_img", img_tag["src"]))
        except Exception:
            pass

    image_field = getattr(entry, "image", None)
    if isinstance(image_field, dict):
        url = image_field.get("href") or image_field.get("url")
        if _looks_like_image_url(url or ""):
            candidates.append(("entry.image", url))

    itunes_image = getattr(entry, "itunes_image", None)
    if isinstance(itunes_image, dict):
        url = itunes_image.get("href")
        if _looks_like_image_url(url or ""):
            candidates.append(("itunes_image", url))

    for link in getattr(entry, "links", None) or []:
        if not isinstance(link, dict):
            continue
        if link.get("rel") == "enclosure":
            url = link.get("href")
            mime = link.get("type", "")
            if _looks_like_image_url(url or "", mime):
                candidates.append(("link[rel=enclosure]", url))

    return candidates


class Command(BaseCommand):
    help = (
        "Fetch every active RSS source, rewrite each new article with Gemini, "
        "and store the result as a pending NewsArticle."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        load_dotenv(settings.BASE_DIR / ".env")

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your_api_key_here":
            raise CommandError(
                "GEMINI_API_KEY is not set. Add it to your .env file."
            )

        model_name = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=GEMINI_REQUEST_TIMEOUT * 1000,  # google-genai expects ms
            ),
        )
        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
        )
        self.stdout.write(f"Using Gemini model: {model_name}")

        sources = RssSource.objects.filter(is_active=True)
        if not sources.exists():
            self.stdout.write(self.style.WARNING("No active RSS sources found."))
            return

        totals = {"created": 0, "skipped": 0, "errors": 0}

        for source in sources:
            self.stdout.write(
                self.style.MIGRATE_HEADING(f"\n>>> {source.name} ({source.url})")
            )

            try:
                feed = feedparser.parse(source.url)
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  Could not parse feed: {exc!r}")
                )
                totals["errors"] += 1
                continue

            if feed.bozo and not feed.entries:
                self.stderr.write(
                    self.style.ERROR(
                        f"  Feed could not be loaded ({feed.bozo_exception!r})."
                    )
                )
                totals["errors"] += 1
                continue

            for entry in feed.entries:
                stats = self._process_entry(
                    entry, source, client, model_name, generation_config,
                )
                for key, value in stats.items():
                    totals[key] += value

        self.stdout.write(
            self.style.SUCCESS(
                "\nDone. "
                f"Created: {totals['created']}, "
                f"skipped (duplicate): {totals['skipped']}, "
                f"errors: {totals['errors']}."
            )
        )

    def _scrape_log(self, message: str, error: bool = False) -> None:
        timestamp = time.strftime("%H:%M:%S")
        stream = self.stderr if error else self.stdout
        style = self.style.ERROR if error else self.style.HTTP_INFO
        stream.write(style(f"  [{timestamp}] [scrape] {message}") + "\n")
        stream.flush()

    def _process_entry(
        self,
        entry: feedparser.FeedParserDict,
        source: RssSource,
        client: "genai.Client",
        model_name: str,
        generation_config: "types.GenerateContentConfig",
    ) -> dict[str, int]:
        stats = {"created": 0, "skipped": 0, "errors": 0}

        link = (getattr(entry, "link", "") or "").strip()
        title = (getattr(entry, "title", "") or "").strip()

        if not link or not title:
            self.stderr.write(self.style.WARNING("  Skipping entry without link/title."))
            stats["errors"] += 1
            return stats

        canonical_url = normalize_article_url(link)
        if not canonical_url:
            self.stderr.write(self.style.WARNING("  Skipping entry with empty URL."))
            stats["errors"] += 1
            return stats

        if link != canonical_url:
            self.stdout.write(
                f"  → URL normalized: {link!r} → {canonical_url!r}"
            )

        if self._article_exists(canonical_url):
            self.stdout.write(
                f"  - duplicate, skipped: {title[:80]} ({canonical_url})"
            )
            stats["skipped"] += 1
            return stats

        raw_html, content_source, scrape_detail = scrape_article_html(
            link, canonical_url, entry, scrape_log=self._scrape_log,
        )
        clean_text = _clean_html_to_text(raw_html)
        if not clean_text:
            clean_text = title

        self.stdout.write(self.style.HTTP_INFO(
            f"  → article content "
            f"| source={content_source} "
            f"| html={len(raw_html)} chars "
            f"| text={len(clean_text)} chars"
            + (f" | {scrape_detail}" if scrape_detail and content_source == "webpage" else "")
        ))
        if content_source == "rss":
            self.stderr.write(self.style.WARNING(
                f"  ! webpage scrape unavailable for '{title[:60]}' "
                f"— using RSS fallback ({scrape_detail})."
            ))

        image_candidates = _extract_image_candidates(entry, raw_html)
        image_url = image_candidates[0][1] if image_candidates else None
        present_fields = [
            attr for attr in (
                "media_thumbnail", "media_content", "enclosures",
                "image", "itunes_image", "links",
            )
            if getattr(entry, attr, None)
        ]
        self.stdout.write(self.style.HTTP_INFO(
            f"  → image scan for '{title[:60]}' "
            f"| entry has: {present_fields or 'none'} "
            f"| candidates: {len(image_candidates)} "
            f"| picked: {image_candidates[0][0] if image_candidates else 'NONE'}"
        ))
        for src, url in image_candidates[:5]:
            self.stdout.write(f"      • {src}: {url}")
        if not image_candidates:
            self.stderr.write(self.style.WARNING(
                f"      ! no image found for '{title[:60]}' — Telegram will "
                "be sent text-only."
            ))

        prompt = PROMPT_TEMPLATE.format(
            channel_id=TELEGRAM_CHANNEL_ID,
            title=title,
            source_name=source.name,
            content=clean_text[:8000],
        )

        self.stdout.write(self.style.HTTP_INFO(
            f"  → Gemini request "
            f"| model={model_name!r} "
            f"| prompt={len(prompt)} chars "
            f"| timeout={GEMINI_REQUEST_TIMEOUT}s "
            f"| config={{response_mime_type={generation_config.response_mime_type!r}, "
            f"temperature={generation_config.temperature}}}"
        ))

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=generation_config,
            )
            raw_text = getattr(response, "text", "") or ""
            if not raw_text.strip():
                raise ValueError("Empty response from Gemini.")

            parsed = json.loads(_strip_markdown_fences(raw_text))
            if not isinstance(parsed, dict):
                raise ValueError("Gemini did not return a JSON object.")

            missing = [k for k in REQUIRED_KEYS if k not in parsed]
            if missing:
                raise ValueError(f"Missing keys in Gemini response: {missing}")

            telegram_text = (parsed.get("telegram_text") or "").strip()
            if TELEGRAM_CHANNEL_ID not in telegram_text:
                telegram_text = f"{telegram_text}\n\n{TELEGRAM_CHANNEL_ID}".strip()

            # Re-check after the (slow) Gemini call — another worker may have
            # inserted the same URL while we were waiting.
            if self._article_exists(canonical_url):
                self.stdout.write(
                    self.style.WARNING(
                        f"  - duplicate after Gemini, skipped: {title[:80]} "
                        f"({canonical_url})"
                    )
                )

                stats["skipped"] += 1
                return stats

            try:
                NewsArticle.objects.create(
                    source=source,
                    original_title=title[:255],
                    original_url=canonical_url,
                    image_url=(image_url or None),
                    site_title=(parsed.get("site_title") or "").strip()[:255] or None,
                    site_lead=(parsed.get("site_lead") or "").strip() or None,
                    site_body=(parsed.get("site_body") or "").strip() or None,
                    telegram_text=telegram_text or None,
                    status=NewsArticle.Status.PENDING,
                )
            except IntegrityError:
                self.stdout.write(
                    self.style.WARNING(
                        f"  - duplicate on save (DB constraint), skipped: "
                        f"{title[:80]} ({canonical_url})"
                    )
                )
                stats["skipped"] += 1
                return stats

            self.stdout.write(self.style.SUCCESS(f"  + created: {title[:80]}"))
            stats["created"] += 1

        except TIMEOUT_EXCEPTIONS as exc:
            self.stderr.write(self.style.ERROR(
                f"  ! Gemini ReadTimeout for '{title[:60]}': "
                f"type={type(exc).__name__} | message={exc!s}"
            ))
            self.stderr.write(self.style.ERROR(
                f"    model={model_name!r}, configured timeout={GEMINI_REQUEST_TIMEOUT}s, "
                f"prompt size={len(prompt)} chars"
            ))
            self.stderr.write(self.style.ERROR(traceback.format_exc()))
            stats["errors"] += 1
        except json.JSONDecodeError as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"  ! Invalid JSON from Gemini for '{title[:60]}': {exc}. Skipping."
                )
            )
            stats["errors"] += 1
        except Exception as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"  ! Gemini/processing failure for '{title[:60]}': {exc!r}. Skipping."
                )
            )
            self.stderr.write(self.style.WARNING(traceback.format_exc()))
            stats["errors"] += 1

        return stats

    @staticmethod
    def _article_exists(canonical_url: str) -> bool:
        """Return True if an article with this canonical URL is already stored."""
        return NewsArticle.objects.filter(original_url=canonical_url).exists()
