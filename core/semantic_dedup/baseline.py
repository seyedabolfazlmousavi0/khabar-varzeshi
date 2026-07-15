"""Fetch and cache 24-hour baseline articles from the Khabar Varzeshi RSS feed."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import mktime
from typing import TYPE_CHECKING, Any, Callable

import feedparser
from django.utils import timezone as dj_timezone

from core.models import BaselineArticleEmbedding
from core.semantic_dedup.text import build_embedding_document, strip_html
from core.semantic_dedup.vectors import l2_normalize

if TYPE_CHECKING:
    from core.semantic_dedup.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BaselineItem:
    guid: str
    url: str
    title: str
    description: str
    pub_date: datetime
    embedding: list[float]


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_entry_pub_date(entry: Any) -> datetime | None:
    published_parsed = getattr(entry, "published_parsed", None)
    if published_parsed is not None:
        try:
            return datetime.fromtimestamp(mktime(published_parsed), tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            pass

    raw = (getattr(entry, "published", "") or getattr(entry, "pubDate", "") or "").strip()
    if not raw:
        return None
    try:
        return _as_aware_utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _entry_guid(entry: Any) -> str:
    guid = (getattr(entry, "id", "") or getattr(entry, "guid", "") or "").strip()
    if guid:
        return guid
    return (getattr(entry, "link", "") or "").strip()


def _entry_description(entry: Any) -> str:
    for attr in ("summary", "description"):
        value = getattr(entry, attr, None)
        if isinstance(value, str) and value.strip():
            return strip_html(value)

    encoded = getattr(entry, "content", None) or []
    for item in encoded:
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return strip_html(value)
    return ""


def _embedding_is_usable(raw: Any) -> bool:
    return isinstance(raw, list) and len(raw) > 0 and all(
        isinstance(v, (int, float)) for v in raw
    )


class BaselineCorpus:
    """In-memory view of the last-N-hours Khabar Varzeshi articles with embeddings."""

    def __init__(self, items: list[BaselineItem]) -> None:
        self.items = items

    def __len__(self) -> int:
        return len(self.items)

    def best_match(self, query_embedding: list[float]) -> tuple[BaselineItem | None, float]:
        from core.semantic_dedup.vectors import cosine_similarity

        best_item: BaselineItem | None = None
        best_score = -1.0
        for item in self.items:
            score = cosine_similarity(query_embedding, item.embedding)
            if score > best_score:
                best_score = score
                best_item = item
        return best_item, best_score if best_item is not None else 0.0


def load_baseline_corpus(
    *,
    rss_url: str,
    lookback_hours: int,
    embedding_service: "EmbeddingService",
    log: Callable[[str], None] | None = None,
) -> BaselineCorpus:
    """Parse the baseline RSS, keep 24h items, and ensure embeddings are cached.

    Cached rows are reused without any Gemini API call. Only missing items are
    embedded, in paced batches, to stay under the free-tier RPM quota.
    """

    def _log(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info(message)

    cutoff = dj_timezone.now() - timedelta(hours=lookback_hours)
    deleted, _ = BaselineArticleEmbedding.objects.filter(pub_date__lt=cutoff).delete()
    if deleted:
        _log(f"purged {deleted} expired baseline embedding(s)")

    try:
        feed = feedparser.parse(rss_url)
    except Exception as exc:
        raise RuntimeError(f"Failed to parse baseline RSS: {exc!r}") from exc

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            f"Baseline RSS could not be loaded ({getattr(feed, 'bozo_exception', None)!r})"
        )

    fresh_entries: list[dict[str, Any]] = []
    for entry in feed.entries:
        guid = _entry_guid(entry)
        title = strip_html(getattr(entry, "title", "") or "")
        link = (getattr(entry, "link", "") or "").strip()
        if not guid or not title:
            continue

        pub_date = parse_entry_pub_date(entry)
        if pub_date is None:
            continue
        pub_date = _as_aware_utc(pub_date)
        if pub_date < cutoff:
            continue

        description = _entry_description(entry)
        fresh_entries.append(
            {
                "guid": guid,
                "url": link or guid,
                "title": title,
                "description": description,
                "pub_date": pub_date,
                "document": build_embedding_document(
                    title=title,
                    description=description,
                ),
            }
        )

    _log(
        f"baseline RSS parsed | within {lookback_hours}h: {len(fresh_entries)} "
        f"| feed entries: {len(feed.entries)}"
    )

    if not fresh_entries:
        return BaselineCorpus([])

    guids = [item["guid"] for item in fresh_entries]
    existing = {
        row.guid: row
        for row in BaselineArticleEmbedding.objects.filter(
            guid__in=guids,
            embedding_model=embedding_service.model,
            pub_date__gte=cutoff,
        )
    }

    cached_by_guid: dict[str, list[float]] = {}
    pending: list[dict[str, Any]] = []

    for entry in fresh_entries:
        row = existing.get(entry["guid"])
        if row is not None and _embedding_is_usable(row.embedding):
            cached_by_guid[entry["guid"]] = l2_normalize(
                [float(v) for v in row.embedding]
            )
        else:
            pending.append(entry)

    _log(
        f"baseline cache | hit={len(cached_by_guid)} | miss={len(pending)} "
        f"| will call Gemini only for misses"
    )

    if pending:
        _log(
            f"embedding {len(pending)} baseline article(s) "
            f"(batch_size={embedding_service.batch_size}, "
            f"interval={embedding_service.request_interval_seconds:.1f}s)"
        )
        documents = [entry["document"] for entry in pending]
        vectors = embedding_service.embed_many(
            documents,
            task_type="SEMANTIC_SIMILARITY",
        )
        if len(vectors) != len(pending):
            raise RuntimeError(
                f"Embedding count mismatch: got {len(vectors)}, "
                f"expected {len(pending)}"
            )

        for entry, embedding in zip(pending, vectors):
            BaselineArticleEmbedding.objects.update_or_create(
                guid=entry["guid"],
                defaults={
                    "url": entry["url"][:500],
                    "title": entry["title"][:500],
                    "description": entry["description"],
                    "pub_date": entry["pub_date"],
                    "embedding_model": embedding_service.model,
                    "embedding": embedding,
                },
            )
            cached_by_guid[entry["guid"]] = embedding

    items: list[BaselineItem] = []
    for entry in fresh_entries:
        embedding = cached_by_guid.get(entry["guid"])
        if embedding is None:
            _log(f"skip baseline item without embedding: {entry['title'][:80]!r}")
            continue
        items.append(
            BaselineItem(
                guid=entry["guid"],
                url=entry["url"],
                title=entry["title"],
                description=entry["description"],
                pub_date=entry["pub_date"],
                embedding=embedding,
            )
        )

    _log(
        f"baseline embeddings ready | total={len(items)} "
        f"| new={len(pending)} | cached={len(cached_by_guid) - len(pending)}"
    )
    return BaselineCorpus(items)
