"""Semantic deduplication against the Khabar Varzeshi 24-hour baseline feed."""

from core.semantic_dedup.filter import (
    SemanticDedupFilter,
    SemanticMatchResult,
    build_semantic_dedup_filter,
)

__all__ = [
    "SemanticDedupFilter",
    "SemanticMatchResult",
    "build_semantic_dedup_filter",
]
