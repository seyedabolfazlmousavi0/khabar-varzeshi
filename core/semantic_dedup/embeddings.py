"""Gemini embedding client used by the semantic deduplication filter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.genai import types

from core.semantic_dedup.vectors import l2_normalize

if TYPE_CHECKING:
    from google import genai


class EmbeddingService:
    """Thin wrapper around ``client.models.embed_content``."""

    def __init__(
        self,
        client: "genai.Client",
        *,
        model: str,
        output_dimensionality: int = 768,
    ) -> None:
        self.client = client
        self.model = model
        self.output_dimensionality = output_dimensionality

    def embed_text(self, text: str, *, task_type: str = "SEMANTIC_SIMILARITY") -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("Cannot embed empty text.")

        response = self.client.models.embed_content(
            model=self.model,
            contents=cleaned,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.output_dimensionality,
            ),
        )

        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            raise ValueError("Embedding API returned no vectors.")

        values = getattr(embeddings[0], "values", None)
        if not values:
            raise ValueError("Embedding API returned an empty vector.")

        return l2_normalize([float(v) for v in values])

    def embed_many(
        self,
        texts: list[str],
        *,
        task_type: str = "SEMANTIC_SIMILARITY",
    ) -> list[list[float]]:
        """Embed texts one-by-one (safe across SDK batch-shape differences)."""
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(self.embed_text(text, task_type=task_type))
        return vectors
