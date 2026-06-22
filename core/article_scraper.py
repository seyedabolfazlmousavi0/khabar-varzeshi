"""Article-page scraper using headless Chrome (Selenium) with stealth options."""

from __future__ import annotations

import os
import random
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeAlias
from urllib.parse import urlparse, urlunparse

import feedparser
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from selenium.webdriver.chrome.webdriver import WebDriver

ScrapeLogger: TypeAlias = Callable[[str, bool], None] | None

MIN_ARTICLE_TEXT_CHARS = 100

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_ARTICLE_BODY_SELECTORS = (
    ".story-text",
    ".story-body",
    ".story",
    "article",
    "[role='main']",
    "main",
    ".article-body",
    ".article__body",
    ".article-content",
    ".article__content",
    ".entry-content",
    ".post-content",
    ".story-body",
    ".content-body",
    ".news-body",
    ".news-content",
    "#article-body",
    "#main-content",
    ".main-content",
)

_NOISE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    ".sidebar",
    ".comments",
    ".comment",
    ".comment-list",
    ".related",
    ".related-articles",
    ".advertisement",
    ".ad",
    ".ads",
    ".social-share",
    ".share-buttons",
    ".newsletter",
    ".breadcrumb",
    ".breadcrumbs",
)

_host_last_request: dict[str, float] = {}
_rate_limit_lock = threading.Lock()


def _default_scrape_log(message: str, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [scrape] {message}", file=stream, flush=True)


def _scrape_settings() -> dict[str, float | str | None]:
    return {
        "user_agent": (os.getenv("SCRAPE_USER_AGENT") or _DEFAULT_USER_AGENT).strip(),
        "proxy": (os.getenv("SCRAPE_PROXY") or "").strip() or None,
        "min_delay": float(os.getenv("SCRAPE_MIN_DELAY", "8")),
        "max_delay": float(os.getenv("SCRAPE_MAX_DELAY", "18")),
        "page_load_timeout": float(os.getenv("SCRAPE_PAGE_LOAD_TIMEOUT", "30")),
        "ready_wait_timeout": float(os.getenv("SCRAPE_READY_WAIT_TIMEOUT", "15")),
        "retry_delay_min": float(os.getenv("SCRAPE_RETRY_DELAY_MIN", "25")),
        "retry_delay_max": float(os.getenv("SCRAPE_RETRY_DELAY_MAX", "45")),
        "chrome_binary": (os.getenv("CHROME_BINARY") or "").strip() or None,
        "chromedriver_path": (os.getenv("CHROMEDRIVER_PATH") or "").strip() or None,
    }


def primary_article_url(original_url: str) -> str:
    """Use the RSS link exactly as published — never the normalized dedup form."""
    return (original_url or "").strip()


def alternate_article_url(original_url: str) -> str | None:
    """Return a single www-prefixed alternate when the RSS link omitted www."""
    parsed = urlparse(original_url)
    host = (parsed.netloc or "").lower()
    if not host or host.startswith("www."):
        return None
    return urlunparse(parsed._replace(netloc=f"www.{host}"))


def _await_host_cooldown(
    url: str,
    settings: dict[str, float | str | None],
    log: ScrapeLogger,
) -> None:
    host = urlparse(url).netloc.lower()
    if not host:
        return

    gap = random.uniform(float(settings["min_delay"]), float(settings["max_delay"]))

    with _rate_limit_lock:
        now = time.monotonic()
        last = _host_last_request.get(host, 0.0)
        wait = last + gap - now

    if wait > 0:
        (log or _default_scrape_log)(
            f"human-like cooldown {wait:.1f}s before contacting {host}"
        )
        time.sleep(wait)


def _record_host_request(url: str) -> None:
    host = urlparse(url).netloc.lower()
    if not host:
        return
    with _rate_limit_lock:
        _host_last_request[host] = time.monotonic()


def _create_stealth_chrome_driver(
    settings: dict[str, float | str | None],
    log: ScrapeLogger,
) -> WebDriver:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    user_agent = str(settings["user_agent"])
    proxy = settings["proxy"]
    chrome_binary = settings["chrome_binary"]
    chromedriver_path = settings["chromedriver_path"]

    (log or _default_scrape_log)("selenium: building headless Chrome options")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=fa-IR")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.add_experimental_option(
        "excludeSwitches",
        ["enable-automation", "enable-logging"],
    )
    options.add_experimental_option("useAutomationExtension", False)

    if chrome_binary:
        options.binary_location = str(chrome_binary)
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
        (log or _default_scrape_log)(f"selenium: proxy enabled ({proxy})")

    driver_path = chromedriver_path or ChromeDriverManager().install()
    (log or _default_scrape_log)(f"selenium: chromedriver={driver_path}")

    driver = webdriver.Chrome(
        service=Service(driver_path, log_output=os.devnull),
        options=options,
    )
    driver.set_page_load_timeout(float(settings["page_load_timeout"]))
    driver.implicitly_wait(0)

    (log or _default_scrape_log)("selenium: applying CDP User-Agent override")
    driver.execute_cdp_cmd(
        "Network.setUserAgentOverride",
        {
            "userAgent": user_agent,
            "acceptLanguage": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            "platform": "Windows",
        },
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": (
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            ),
        },
    )

    return driver


def _validate_page_source(body: str, url: str) -> tuple[str, str]:
    if not body or not body.strip():
        return "", f"{url}: empty page_source"
    if len(body.strip()) < 200:
        return "", f"{url}: page_source too short ({len(body)} bytes)"
    return body, ""


def _fetch_via_selenium(
    url: str,
    settings: dict[str, float | str | None],
    log: ScrapeLogger,
) -> tuple[str, str, str]:
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.support.ui import WebDriverWait

    if not url:
        return "", "empty url", "selenium"

    driver: WebDriver | None = None
    started = time.monotonic()

    try:
        (log or _default_scrape_log)(f"selenium: launch | url={url}")
        driver = _create_stealth_chrome_driver(settings, log)

        (log or _default_scrape_log)(f"selenium: navigate → {url}")
        driver.get(url)

        ready_timeout = float(settings["ready_wait_timeout"])
        (log or _default_scrape_log)(
            f"selenium: waiting for document.readyState (timeout={ready_timeout}s)"
        )
        WebDriverWait(driver, ready_timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        settle = random.uniform(1.0, 2.5)
        (log or _default_scrape_log)(f"selenium: post-load settle {settle:.1f}s")
        time.sleep(settle)

        (log or _default_scrape_log)("selenium: reading driver.page_source")
        page_html = driver.page_source or ""
        elapsed = time.monotonic() - started

        html, error = _validate_page_source(page_html, url)
        if html:
            (log or _default_scrape_log)(
                f"selenium: ✓ page_source captured in {elapsed:.2f}s | bytes={len(html)}"
            )
            return html, "", "selenium/headless-chrome"

        (log or _default_scrape_log)(
            f"selenium: ✗ invalid page_source in {elapsed:.2f}s | {error}",
            error=True,
        )
        return "", error, "selenium/headless-chrome"

    except TimeoutException as exc:
        elapsed = time.monotonic() - started
        error = f"{url}: page load timeout after {elapsed:.2f}s ({exc})"
        (log or _default_scrape_log)(f"selenium: ✗ {error}", error=True)
        return "", error, "selenium/headless-chrome"
    except WebDriverException as exc:
        elapsed = time.monotonic() - started
        error = f"{url}: WebDriverException: {exc} ({elapsed:.2f}s)"
        (log or _default_scrape_log)(f"selenium: ✗ {error}", error=True)
        return "", error, "selenium/headless-chrome"
    except Exception as exc:
        elapsed = time.monotonic() - started
        error = f"{url}: {type(exc).__name__}: {exc} ({elapsed:.2f}s)"
        (log or _default_scrape_log)(f"selenium: ✗ {error}", error=True)
        return "", error, "selenium/headless-chrome"
    finally:
        if driver is not None:
            (log or _default_scrape_log)("selenium: quitting driver")
            try:
                driver.quit()
            except Exception as exc:
                (log or _default_scrape_log)(
                    f"selenium: driver.quit() failed: {exc}",
                    error=True,
                )


def _human_retry_pause(
    settings: dict[str, float | str | None],
    log: ScrapeLogger,
    reason: str,
) -> None:
    wait = random.uniform(
        float(settings["retry_delay_min"]),
        float(settings["retry_delay_max"]),
    )
    (log or _default_scrape_log)(
        f"backing off {wait:.1f}s after failure ({reason}) before alternate URL"
    )
    time.sleep(wait)


def fetch_article_page_html(
    url: str,
    scrape_log: ScrapeLogger = None,
) -> tuple[str, str, str]:
    """Fetch article HTML via headless Chrome. Returns (html, error, method)."""
    log = scrape_log or _default_scrape_log
    settings = _scrape_settings()

    if not url:
        return "", "empty url", ""

    log(f"selenium pipeline | url={url}")
    _await_host_cooldown(url, settings, log)

    html, error, method = _fetch_via_selenium(url, settings, log)
    _record_host_request(url)

    if html:
        log(f"✓ SUCCESS via {method} | bytes={len(html)}")
        return html, "", method

    log(f"✗ FAILED via {method} | {error}", error=True)
    return "", error, method


def _clean_html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""

    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _decompose_noise(soup: BeautifulSoup) -> None:
    for selector in _NOISE_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()


def extract_article_body_html(page_html: str) -> str:
    if not page_html:
        return ""

    soup = BeautifulSoup(page_html, "html.parser")
    _decompose_noise(soup)

    best_element = None
    best_length = 0

    for selector in _ARTICLE_BODY_SELECTORS:
        for element in soup.select(selector):
            text_length = len(element.get_text(strip=True))
            if text_length > best_length:
                best_length = text_length
                best_element = element

    if best_element is not None and best_length >= MIN_ARTICLE_TEXT_CHARS:
        return str(best_element)

    body = soup.find("body")
    if body is not None:
        body_length = len(body.get_text(strip=True))
        if body_length >= MIN_ARTICLE_TEXT_CHARS:
            return str(body)

    return ""


def _extract_raw_html_from_entry(entry: feedparser.FeedParserDict) -> str:
    content = getattr(entry, "content", None)
    if content:
        try:
            return content[0].get("value", "") or ""
        except (AttributeError, IndexError, TypeError):
            pass

    for attr in ("summary", "description", "subtitle"):
        value = getattr(entry, attr, None)
        if value:
            return value

    return ""


def scrape_article_html(
    original_url: str,
    canonical_url: str,
    entry: feedparser.FeedParserDict,
    scrape_log: ScrapeLogger = None,
) -> tuple[str, str, str]:
    """Return (html, source, detail) — Selenium webpage fetch with RSS fallback."""
    log = scrape_log or _default_scrape_log
    settings = _scrape_settings()

    primary = primary_article_url(original_url) or canonical_url
    log(
        f"resolve article | primary={primary} "
        f"| canonical(dedup only)={canonical_url}"
    )

    page_html, fetch_error, fetch_method = fetch_article_page_html(
        primary,
        scrape_log=log,
    )

    if not page_html:
        alternate = alternate_article_url(primary)
        if alternate and alternate != primary:
            _human_retry_pause(settings, log, fetch_error)
            log(f"single alternate selenium attempt | url={alternate}")
            page_html, fetch_error, fetch_method = fetch_article_page_html(
                alternate,
                scrape_log=log,
            )

    if page_html:
        log("extracting article body from page_source via BeautifulSoup")
        article_html = extract_article_body_html(page_html)
        article_text_len = len(_clean_html_to_text(article_html))
        log(f"extracted body text length={article_text_len}")
        if article_text_len >= MIN_ARTICLE_TEXT_CHARS:
            detail = f"via {fetch_method}" if fetch_method else ""
            return article_html, "webpage", detail

        log(
            "page downloaded but extracted body was too short — RSS fallback",
            error=True,
        )
        return (
            _extract_raw_html_from_entry(entry),
            "rss",
            "webpage downloaded but article body was too short",
        )

    detail = fetch_error or "webpage download failed"
    log(f"selenium scrape failed — RSS fallback ({detail})", error=True)
    return _extract_raw_html_from_entry(entry), "rss", detail
