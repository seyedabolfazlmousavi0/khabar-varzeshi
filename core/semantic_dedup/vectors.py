"""Plain-Python vector helpers for semantic similarity."""

from __future__ import annotations

import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in ``[-1, 1]`` (``0`` if either vector is empty)."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def l2_normalize(vector: list[float]) -> list[float]:
    """Return an L2-normalized copy of ``vector`` (or the original if empty)."""
    if not vector:
        return vector
    norm = math.sqrt(sum(x * x for x in vector))
    if norm <= 0.0:
        return list(vector)
    return [x / norm for x in vector]
