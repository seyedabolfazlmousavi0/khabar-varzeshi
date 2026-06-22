from django.test import SimpleTestCase

from core.management.commands.fetch_news import _build_article_fetch_urls
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
