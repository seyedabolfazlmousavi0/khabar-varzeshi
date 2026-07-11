"""Build article payloads and run the newsroom publish pipeline."""

from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from core.bot.services import load_article_for_bot, resolve_image_url
from core.models import NewsArticle
from core.newsroom.automation import NewsroomAutomation
from core.newsroom.config import NewsroomConfig, load_newsroom_config
from core.newsroom.exceptions import SitePublishError

logger = logging.getLogger(__name__)

_IMAGE_DOWNLOAD_TIMEOUT = 30


@dataclass(frozen=True)
class ArticlePublishPayload:
    headline: str
    lead: str
    body: str
    image_path: str | None = None
    image_caption: str | None = None


def _guess_image_suffix(url: str, content_type: str | None) -> str:
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed

    path_suffix = Path(urlparse(url).path).suffix
    if path_suffix:
        return path_suffix

    return ".jpg"


def _download_image_to_dir(image_url: str, dest_dir: Path) -> Path:
    response = requests.get(image_url, timeout=_IMAGE_DOWNLOAD_TIMEOUT)
    response.raise_for_status()

    suffix = _guess_image_suffix(image_url, response.headers.get("Content-Type"))
    image_path = dest_dir / f"article_image{suffix}"
    image_path.write_bytes(response.content)
    return image_path.resolve()


def build_payload_from_article(article: NewsArticle) -> ArticlePublishPayload:
    """Validate article site fields and prepare a publish payload."""
    headline = (article.site_title or "").strip()
    lead = (article.site_lead or "").strip()
    body = (article.site_body or "").strip()

    missing: list[str] = []
    if not headline:
        missing.append("site_title")
    if not lead:
        missing.append("site_lead")
    if not body:
        missing.append("site_body")

    if missing:
        raise SitePublishError(
            f"Article #{article.id} is missing required site fields: "
            f"{', '.join(missing)}"
        )

    image_url = resolve_image_url(article)
    image_path: str | None = None
    if image_url:
        temp_dir = Path(tempfile.mkdtemp(prefix=f"newsroom_article_{article.id}_"))
        try:
            local_image = _download_image_to_dir(image_url, temp_dir)
            image_path = str(local_image)
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise SitePublishError(
                f"Failed to download article image from {image_url!r}: {exc}"
            ) from exc
    else:
        logger.warning(
            "Article #%s has no image_url — publishing without image upload.",
            article.id,
        )

    return ArticlePublishPayload(
        headline=headline,
        lead=lead,
        body=body,
        image_path=image_path,
        image_caption=headline,
    )


def publish_article_to_site(
    payload: ArticlePublishPayload,
    *,
    config: NewsroomConfig | None = None,
) -> None:
    """Run the Selenium workflow for one article payload."""
    newsroom_config = config or load_newsroom_config()
    automation = NewsroomAutomation(newsroom_config)

    try:
        automation.publish(
            headline=payload.headline,
            lead=payload.lead,
            body=payload.body,
            image_path=payload.image_path,
            image_caption=payload.image_caption,
        )
    finally:
        if payload.image_path:
            temp_root = Path(payload.image_path).parent
            if temp_root.name.startswith("newsroom_article_"):
                shutil.rmtree(temp_root, ignore_errors=True)


def publish_article_by_id(
    article_id: int,
    *,
    config: NewsroomConfig | None = None,
) -> None:
    """Load an article from the database and publish it to the newsroom."""
    article = load_article_for_bot(article_id)
    payload = build_payload_from_article(article)
    publish_article_to_site(payload, config=config)
