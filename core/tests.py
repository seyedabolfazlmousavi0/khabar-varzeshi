from django.test import SimpleTestCase

from core.article_scraper import (
    _validate_page_source,
    alternate_article_url,
    extract_article_body_html,
    primary_article_url,
)
from core.url_utils import normalize_article_url


class ArticleFetchUrlTests(SimpleTestCase):
    def test_primary_url_uses_rss_link_not_normalized_form(self):
        original = (
            "https://www.tasnimnews.com/fa/news/1404/03/31/3234567/"
            "sample-title/"
        )
        canonical = normalize_article_url(original)

        self.assertEqual(primary_article_url(original), original)
        self.assertNotEqual(primary_article_url(original), canonical)

    def test_alternate_url_adds_www_when_missing(self):
        original = "https://tasnimnews.com/fa/news/1404/03/31/123/test"
        alternate = alternate_article_url(original)
        self.assertEqual(alternate, "https://www.tasnimnews.com/fa/news/1404/03/31/123/test")

    def test_alternate_url_none_when_www_present(self):
        original = "https://www.tasnimnews.ir/fa/news/1404/03/31/123/test"
        self.assertIsNone(alternate_article_url(original))


class PageSourceValidationTests(SimpleTestCase):
    def test_validate_page_source_rejects_empty_html(self):
        html, error = _validate_page_source("", "https://example.com/article")
        self.assertEqual(html, "")
        self.assertIn("empty", error)

    def test_extract_article_body_from_story_markup(self):
        page = """
        <html><body>
          <nav>menu</nav>
          <div class="story-text">
            <p>""" + ("خبر ورزشی " * 40) + """</p>
          </div>
        </body></html>
        """
        body = extract_article_body_html(page)
        self.assertIn("story-text", body)
        self.assertGreater(len(body), 100)
