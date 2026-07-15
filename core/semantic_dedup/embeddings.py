"""Gemini embedding client with free-tier rate-limit pacing and 429 retries."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from google.genai import types

from core.semantic_dedup.vectors import l2_normalize

if TYPE_CHECKING:
    from google import genai

logger = logging.getLogger(__name__)

# Free-tier embed quota is ~100 requests/minute. Stay well under it.
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.2
DEFAULT_BATCH_SIZE = 8
DEFAULT_BATCH_PAUSE_SECONDS = 2.0
DEFAULT_429_MAX_RETRIES = 5
DEFAULT_429_BASE_DELAY_SECONDS = 12.0
DEFAULT_429_MAX_DELAY_SECONDS = 60.0


def is_rate_limit_error(exc: BaseException) -> bool:
    """True for Gemini free-tier / RESOURCE_EXHAUSTED style failures."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status_code", None)
    if code == 429 or status == 429:
        return True

    message = str(exc).upper()
    return (
        "429" in message
        or "RESOURCE_EXHAUSTED" in message
        or "RATE LIMIT" in message
        or "QUOTA EXCEEDED" in message
    )


class EmbeddingService:
    """Thin wrapper around ``client.models.embed_content`` with pacing + backoff."""

    def __init__(
        self,
        client: "genai.Client",
        *,
        model: str,
        output_dimensionality: int = 768,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
        max_retries: int = DEFAULT_429_MAX_RETRIES,
        retry_base_delay_seconds: float = DEFAULT_429_BASE_DELAY_SECONDS,
        retry_max_delay_seconds: float = DEFAULT_429_MAX_DELAY_SECONDS,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.output_dimensionality = output_dimensionality
        self.request_interval_seconds = max(0.0, float(request_interval_seconds))
        self.batch_size = max(1, int(batch_size))
        self.batch_pause_seconds = max(0.0, float(batch_pause_seconds))
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay_seconds = max(1.0, float(retry_base_delay_seconds))
        self.retry_max_delay_seconds = max(
            self.retry_base_delay_seconds,
            float(retry_max_delay_seconds),
        )
        self._log = log
        self._last_request_monotonic: float | None = None

    def _emit(self, message: str) -> None:
        if self._log:
            self._log(message)
        else:
            logger.info("[embed] %s", message)

    def _pace(self) -> None:
        """Sleep so consecutive API calls stay under the free-tier RPM limit."""
        if self.request_interval_seconds <= 0:
            return
        now = time.monotonic()
        if self._last_request_monotonic is not None:
            elapsed = now - self._last_request_monotonic
            remaining = self.request_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _mark_request(self) -> None:
        self._last_request_monotonic = time.monotonic()

    def _retry_delay(self, attempt: int) -> float:
        # attempt is 1-based after first failure
        delay = self.retry_base_delay_seconds * (2 ** (attempt - 1))
        return min(delay, self.retry_max_delay_seconds)

    def embed_text(self, text: str, *, task_type: str = "SEMANTIC_SIMILARITY") -> list[float]:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("Cannot embed empty text.")

        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                response = self.client.models.embed_content(
                    model=self.model,
                    contents=cleaned,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.output_dimensionality,
                    ),
                )
            except Exception as exc:
                self._mark_request()
                last_error = exc
                if not is_rate_limit_error(exc) or attempt >= self.max_retries:
                    raise

                delay = self._retry_delay(attempt + 1)
                self._emit(
                    f"429 RESOURCE_EXHAUSTED — waiting {delay:.0f}s "
                    f"then retry {attempt + 1}/{self.max_retries}"
                )
                time.sleep(delay)
                continue

            self._mark_request()

            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                raise ValueError("Embedding API returned no vectors.")

            values = getattr(embeddings[0], "values", None)
            if not values:
                raise ValueError("Embedding API returned an empty vector.")

            return l2_normalize([float(v) for v in values])

        assert last_error is not None
        raise last_error

    def embed_many(
        self,
        texts: list[str],
        *,
        task_type: str = "SEMANTIC_SIMILARITY",
    ) -> list[list[float]]:
        """Embed texts with per-request pacing and short pauses between batches."""
        vectors: list[list[float]] = []
        total = len(texts)
        if total == 0:
            return vectors

        for index, text in enumerate(texts, start=1):
            vectors.append(self.embed_text(text, task_type=task_type))

            # Extra pause after each completed batch (except the final item).
            if (
                index < total
                and self.batch_size > 0
                and index % self.batch_size == 0
                and self.batch_pause_seconds > 0
            ):
                self._emit(
                    f"batch pause {self.batch_pause_seconds:.1f}s "
                    f"after {index}/{total} embeddings"
                )
                time.sleep(self.batch_pause_seconds)

        return vectors
