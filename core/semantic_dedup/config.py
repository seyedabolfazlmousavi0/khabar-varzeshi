"""Configuration for semantic deduplication against the site RSS baseline."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_BASELINE_RSS_URL = "https://www.khabarvarzeshi.com/rss"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_SIMILARITY_THRESHOLD = 0.68
DEFAULT_OUTPUT_DIMENSIONALITY = 768
DEFAULT_LOOKBACK_HOURS = 24


@dataclass(frozen=True)
class SemanticDedupConfig:
    enabled: bool
    baseline_rss_url: str
    embedding_model: str
    similarity_threshold: float
    output_dimensionality: int
    lookback_hours: int
    fail_open: bool


def load_semantic_dedup_config() -> SemanticDedupConfig:
    enabled_raw = os.getenv("SEMANTIC_DEDUP_ENABLED", "1").strip().lower()
    fail_open_raw = os.getenv("SEMANTIC_DEDUP_FAIL_OPEN", "1").strip().lower()

    return SemanticDedupConfig(
        enabled=enabled_raw in {"1", "true", "yes", "on"},
        baseline_rss_url=os.getenv(
            "SEMANTIC_DEDUP_BASELINE_RSS",
            DEFAULT_BASELINE_RSS_URL,
        ).strip()
        or DEFAULT_BASELINE_RSS_URL,
        embedding_model=os.getenv(
            "SEMANTIC_DEDUP_EMBEDDING_MODEL",
            DEFAULT_EMBEDDING_MODEL,
        ).strip()
        or DEFAULT_EMBEDDING_MODEL,
        similarity_threshold=float(
            os.getenv(
                "SEMANTIC_DEDUP_THRESHOLD",
                str(DEFAULT_SIMILARITY_THRESHOLD),
            )
        ),
        output_dimensionality=int(
            os.getenv(
                "SEMANTIC_DEDUP_DIMENSIONS",
                str(DEFAULT_OUTPUT_DIMENSIONALITY),
            )
        ),
        lookback_hours=int(
            os.getenv(
                "SEMANTIC_DEDUP_LOOKBACK_HOURS",
                str(DEFAULT_LOOKBACK_HOURS),
            )
        ),
        fail_open=fail_open_raw in {"1", "true", "yes", "on"},
    )
