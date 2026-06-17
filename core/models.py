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
