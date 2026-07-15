from django.db import models

from core.url_utils import normalize_article_url


class RssSource(models.Model):
    name = models.CharField(max_length=100)
    url = models.URLField(unique=True)
    category = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "RSS Source"
        verbose_name_plural = "RSS Sources"
        ordering = ["name"]

    def __str__(self):
        return self.name


class NewsArticle(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"

    source = models.ForeignKey(
        RssSource,
        on_delete=models.CASCADE,
        related_name="articles",
    )
    original_title = models.CharField(max_length=255)
    original_url = models.URLField(unique=True)
    image_url = models.URLField(max_length=500, null=True, blank=True)
    site_title = models.CharField(max_length=255, null=True, blank=True)
    site_lead = models.TextField(null=True, blank=True)
    site_body = models.TextField(null=True, blank=True)
    telegram_text = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "News Article"
        verbose_name_plural = "News Articles"
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_title

    def save(self, *args, **kwargs):
        if self.original_url:
            self.original_url = normalize_article_url(self.original_url)
        super().save(*args, **kwargs)


class BaselineArticleEmbedding(models.Model):
    """Cached embedding for a Khabar Varzeshi RSS item from the last 24 hours."""

    guid = models.CharField(max_length=500, unique=True, db_index=True)
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    pub_date = models.DateTimeField(db_index=True)
    embedding_model = models.CharField(max_length=100)
    embedding = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Baseline Article Embedding"
        verbose_name_plural = "Baseline Article Embeddings"
        ordering = ["-pub_date"]
        indexes = [
            models.Index(fields=["pub_date", "embedding_model"]),
        ]

    def __str__(self) -> str:
        return self.title[:80]
