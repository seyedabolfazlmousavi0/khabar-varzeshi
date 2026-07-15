"""Semantic duplicate filter used by the news ingestion worker."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from core.semantic_dedup.baseline import BaselineCorpus, load_baseline_corpus
from core.semantic_dedup.config import SemanticDedupConfig, load_semantic_dedup_config
from core.semantic_dedup.embeddings import EmbeddingService
from core.semantic_dedup.text import build_embedding_document, entry_description

if TYPE_CHECKING:
    from google import genai

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticMatchResult:
    is_duplicate: bool
    similarity: float
    matched_title: str = ""
    matched_url: str = ""
    skipped_due_to_error: bool = False
    detail: str = ""


class SemanticDedupFilter:
    """Compare incoming articles against the 24h Khabar Varzeshi baseline corpus."""

    def __init__(
        self,
        *,
        config: SemanticDedupConfig,
        corpus: BaselineCorpus | None,
        embedding_service: EmbeddingService | None,
        unavailable_reason: str = "",
    ) -> None:
        self.config = config
        self.corpus = corpus
        self.embedding_service = embedding_service
        self.unavailable_reason = unavailable_reason

    @property
    def is_ready(self) -> bool:
        return (
            self.config.enabled
            and self.corpus is not None
            and self.embedding_service is not None
            and not self.unavailable_reason
        )

    def check_entry(self, entry: Any) -> SemanticMatchResult:
        title = (getattr(entry, "title", "") or "").strip()
        description = entry_description(entry)
        return self.check_text(title=title, description=description)

    def check_text(self, *, title: str, description: str = "") -> SemanticMatchResult:
        if not self.config.enabled:
            return SemanticMatchResult(
                is_duplicate=False,
                similarity=0.0,
                detail="semantic dedup disabled",
            )

        if not self.is_ready or self.embedding_service is None or self.corpus is None:
            reason = self.unavailable_reason or "semantic dedup unavailable"
            if self.config.fail_open:
                return SemanticMatchResult(
                    is_duplicate=False,
                    similarity=0.0,
                    skipped_due_to_error=True,
                    detail=f"fail-open: {reason}",
                )
            return SemanticMatchResult(
                is_duplicate=True,
                similarity=0.0,
                skipped_due_to_error=True,
                detail=f"fail-closed: {reason}",
            )

        if len(self.corpus) == 0:
            return SemanticMatchResult(
                is_duplicate=False,
                similarity=0.0,
                detail="empty baseline corpus",
            )

        document = build_embedding_document(title=title, description=description)
        if not document:
            return SemanticMatchResult(
                is_duplicate=False,
                similarity=0.0,
                detail="empty incoming document",
            )

        try:
            query_embedding = self.embedding_service.embed_text(
                document,
                task_type="SEMANTIC_SIMILARITY",
            )
            matched, score = self.corpus.best_match(query_embedding)
        except Exception as exc:
            logger.exception("Incoming article embedding failed")
            if self.config.fail_open:
                return SemanticMatchResult(
                    is_duplicate=False,
                    similarity=0.0,
                    skipped_due_to_error=True,
                    detail=f"fail-open after embed error: {exc}",
                )
            return SemanticMatchResult(
                is_duplicate=True,
                similarity=0.0,
                skipped_due_to_error=True,
                detail=f"fail-closed after embed error: {exc}",
            )

        if matched is None:
            return SemanticMatchResult(
                is_duplicate=False,
                similarity=0.0,
                detail="no baseline match",
            )

        is_duplicate = score >= self.config.similarity_threshold
        return SemanticMatchResult(
            is_duplicate=is_duplicate,
            similarity=score,
            matched_title=matched.title,
            matched_url=matched.url,
            detail=(
                f"match score={score:.4f} "
                f"threshold={self.config.similarity_threshold:.2f}"
            ),
        )


def build_semantic_dedup_filter(
    client: "genai.Client",
    *,
    config: SemanticDedupConfig | None = None,
    log: Callable[[str], None] | None = None,
) -> SemanticDedupFilter:
    """Build a filter and warm the 24h baseline corpus for one worker cycle."""
    cfg = config or load_semantic_dedup_config()

    def _log(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info("[semantic-dedup] %s", message)

    if not cfg.enabled:
        _log("disabled via SEMANTIC_DEDUP_ENABLED")
        return SemanticDedupFilter(
            config=cfg,
            corpus=None,
            embedding_service=None,
            unavailable_reason="disabled",
        )

    embedding_service = EmbeddingService(
        client,
        model=cfg.embedding_model,
        output_dimensionality=cfg.output_dimensionality,
        request_interval_seconds=cfg.request_interval_seconds,
        batch_size=cfg.batch_size,
        batch_pause_seconds=cfg.batch_pause_seconds,
        max_retries=cfg.max_retries,
        retry_base_delay_seconds=cfg.retry_base_delay_seconds,
        retry_max_delay_seconds=cfg.retry_max_delay_seconds,
        log=_log,
    )

    try:
        corpus = load_baseline_corpus(
            rss_url=cfg.baseline_rss_url,
            lookback_hours=cfg.lookback_hours,
            embedding_service=embedding_service,
            log=_log,
        )
    except Exception as exc:
        logger.exception("Failed to build semantic dedup baseline corpus")
        _log(f"baseline load failed: {exc!r} (fail_open={cfg.fail_open})")
        return SemanticDedupFilter(
            config=cfg,
            corpus=None,
            embedding_service=None,
            unavailable_reason=str(exc),
        )

    _log(
        f"ready | baseline={len(corpus)} | model={cfg.embedding_model} "
        f"| threshold={cfg.similarity_threshold:.2f}"
    )
    return SemanticDedupFilter(
        config=cfg,
        corpus=corpus,
        embedding_service=embedding_service,
    )
