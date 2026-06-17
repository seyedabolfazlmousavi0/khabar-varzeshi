"""URL normalization for deduplicating news articles across RSS feeds."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Common tracking / session params that do not identify a unique article.
_STRIP_QUERY_PARAMS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "_ga",
    "from",
})


def normalize_article_url(url: str) -> str:
    """Return a canonical form of ``url`` for deduplication.

    Rules applied (in order):
      - strip whitespace
      - lowercase scheme and hostname
      - strip leading ``www.`` from hostname
      - remove URL fragment (``#...``)
      - remove trailing slash from path (except root ``/``)
      - drop known tracking query parameters
      - sort remaining query parameters for stable comparison
    """
    url = (url or "").strip()
    if not url:
        return url

    parsed = urlparse(url)

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _STRIP_QUERY_PARAMS
    ]
    query_pairs.sort(key=lambda pair: (pair[0].lower(), pair[1]))
    query = urlencode(query_pairs)

    return urlunparse((scheme, netloc, path, "", query, ""))
