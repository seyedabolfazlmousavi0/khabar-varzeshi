"""Remove duplicate NewsArticle rows, keeping the earliest entry per URL.

Groups articles by normalized ``original_url``. Within each group, keeps the
row with the smallest ``created_at`` (ties broken by smallest ``id``) and
deletes the rest.

Run with:

    python manage.py remove_duplicate_articles --dry-run
    python manage.py remove_duplicate_articles
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import NewsArticle
from core.url_utils import normalize_article_url


class Command(BaseCommand):
    help = (
        "Find duplicate NewsArticle rows (by normalized original_url) and "
        "delete all but the earliest entry in each group."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report duplicates without deleting anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]

        by_canonical: dict[str, list[NewsArticle]] = defaultdict(list)
        for article in NewsArticle.objects.order_by("created_at", "id"):
            key = normalize_article_url(article.original_url)
            by_canonical[key].append(article)

        duplicate_groups = {
            key: articles for key, articles in by_canonical.items() if len(articles) > 1
        }

        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicate articles found."))
            return

        to_delete: list[NewsArticle] = []
        for canonical_url, articles in duplicate_groups.items():
            keeper = articles[0]
            extras = articles[1:]
            to_delete.extend(extras)

            self.stdout.write(
                self.style.WARNING(
                    f"\nDuplicate group ({len(articles)} rows) → {canonical_url!r}"
                )
            )
            self.stdout.write(
                f"  KEEP  id={keeper.id} created={keeper.created_at} "
                f"url={keeper.original_url!r}"
            )
            for article in extras:
                self.stdout.write(
                    f"  DEL   id={article.id} created={article.created_at} "
                    f"url={article.original_url!r}"
                )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\nSummary: {len(duplicate_groups)} duplicate group(s), "
                f"{len(to_delete)} row(s) to delete."
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Dry run — no rows were deleted.")
            )
            return

        with transaction.atomic():
            deleted_count, _ = NewsArticle.objects.filter(
                pk__in=[article.pk for article in to_delete]
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted_count} duplicate article(s).")
        )
        self._normalize_remaining_urls()

    def _normalize_remaining_urls(self) -> None:
        """Rewrite ``original_url`` to its canonical form on all remaining rows."""
        updated = 0
        skipped = 0
        for article in NewsArticle.objects.order_by("id"):
            normalized = normalize_article_url(article.original_url)
            if normalized == article.original_url:
                continue
            if (
                NewsArticle.objects.filter(original_url=normalized)
                .exclude(pk=article.pk)
                .exists()
            ):
                self.stderr.write(
                    self.style.WARNING(
                        f"  Skipped normalizing id={article.id}: "
                        f"{normalized!r} already taken."
                    )
                )
                skipped += 1
                continue
            article.original_url = normalized
            article.save(update_fields=["original_url"])
            updated += 1

        if updated or skipped:
            self.stdout.write(
                f"Normalized {updated} URL(s); skipped {skipped} collision(s)."
            )
