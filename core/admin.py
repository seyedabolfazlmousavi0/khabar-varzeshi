from django.contrib import admin
from django.utils.html import format_html

from .models import NewsArticle, RssSource


@admin.register(RssSource)
class RssSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name", "url")
    list_editable = ("is_active",)


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = (
        "original_title", "source", "status", "has_image", "created_at",
    )
    list_filter = ("status", "source")
    search_fields = ("original_title", "site_title")
    date_hierarchy = "created_at"
    list_select_related = ("source",)
    readonly_fields = ("created_at", "image_preview")

    fieldsets = (
        (None, {
            "fields": (
                "source", "status", "created_at",
                "original_title", "original_url",
                "image_url", "image_preview",
            ),
        }),
        ("بازنویسی شده با Gemini", {
            "fields": ("site_title", "site_lead", "site_body", "telegram_text"),
        }),
    )

    @admin.display(boolean=True, description="Has image")
    def has_image(self, obj: NewsArticle) -> bool:
        return bool(obj.image_url)

    @admin.display(description="Image preview")
    def image_preview(self, obj: NewsArticle) -> str:
        if not obj.image_url:
            return "—"
        return format_html(
            '<a href="{0}" target="_blank">'
            '<img src="{0}" style="max-height: 200px; max-width: 320px;" />'
            "</a>",
            obj.image_url,
        )
