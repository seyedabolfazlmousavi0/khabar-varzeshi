from django.test import SimpleTestCase

from core.management.commands.fetch_news import (
    _FETCH_STRATEGIES,
    _build_article_fetch_urls,
    _validate_fetched_page,
)
from core.url_utils import normalize_article_url


class ArticleFetchUrlTests(SimpleTestCase):
    def test_prefers_original_www_url_before_normalized_bare_host(self):
        original = (
            "https://www.tasnimnews.com/fa/news/1404/03/31/3234567/"
            "sample-title/"
        )
        canonical = normalize_article_url(original)

        urls = _build_article_fetch_urls(original, canonical)

        self.assertEqual(urls[0], original)
        self.assertIn("www.tasnimnews.com", urls[0])
        self.assertEqual(canonical, "https://tasnimnews.com/fa/news/1404/03/31/3234567/sample-title")
        self.assertIn(canonical, urls)
        self.assertTrue(
            any(url.startswith("https://www.tasnimnews.com/") for url in urls),
            urls,
        )


class FetchStrategyTests(SimpleTestCase):
    def test_fetch_strategies_include_multiple_backends(self):
        names = [name for name, _, _ in _FETCH_STRATEGIES]
        self.assertIn("requests-session/chrome-same-origin", names)
        self.assertIn("httpx/http1-chrome", names)
        self.assertIn("aiohttp/chrome-same-origin", names)
        self.assertGreaterEqual(len(_FETCH_STRATEGIES), 10)

    def test_validate_fetched_page_rejects_gateway_errors(self):
        html, error = _validate_fetched_page(
            502,
            "<html></html>",
            "text/html",
            "https://example.com/article",
        )
        self.assertEqual(html, "")
        self.assertIn("502", error)
