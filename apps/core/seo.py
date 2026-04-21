from __future__ import annotations

import json
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import Truncator

DEFAULT_SEO_TITLE = "PaliGames"
DEFAULT_SEO_DESCRIPTION = (
    "PaliGames — развивающие игры, материалы для занятий и игры на заказ для детей и родителей."
)


def build_absolute_url(path_or_url: str) -> str:
    if not path_or_url:
        return settings.SITE_BASE_URL.rstrip("/")
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return urljoin(settings.SITE_BASE_URL.rstrip("/") + "/", path_or_url.lstrip("/"))


def get_default_seo_image_url() -> str:
    return build_absolute_url(staticfiles_storage.url("images/logo.svg"))


def normalize_seo_description(text: str, *, fallback: str = DEFAULT_SEO_DESCRIPTION, max_length: int = 160) -> str:
    normalized = " ".join(strip_tags(text or "").split())
    if not normalized:
        normalized = fallback
    return Truncator(normalized).chars(max_length)


def build_breadcrumbs_json_ld(items: list[dict], *, request=None) -> dict | None:
    json_ld_items: list[dict] = []
    position = 1
    for item in items:
        url = item.get("url")
        if not url:
            continue
        json_ld_items.append(
            {
                "@type": "ListItem",
                "position": position,
                "name": item["title"],
                "item": build_absolute_url(url),
            },
        )
        position += 1

    if not json_ld_items:
        return None

    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": json_ld_items,
    }


def serialize_json_ld(data: dict | list | None) -> str:
    if not data:
        return ""
    if isinstance(data, list):
        data = [item for item in data if item]
        if not data:
            return ""
    return mark_safe(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def build_seo_context(  # noqa: PLR0913
    *,
    title: str,
    description: str = DEFAULT_SEO_DESCRIPTION,
    canonical_url: str = "",
    image_url: str = "",
    robots: str = "index,follow",
    og_type: str = "website",
    json_ld: dict | list | None = None,
) -> dict:
    resolved_title = title or DEFAULT_SEO_TITLE
    resolved_canonical_url = build_absolute_url(canonical_url) if canonical_url else settings.SITE_BASE_URL.rstrip("/")
    resolved_image_url = build_absolute_url(image_url) if image_url else get_default_seo_image_url()
    resolved_description = normalize_seo_description(description)

    return {
        "seo_title": resolved_title,
        "seo_description": resolved_description,
        "seo_canonical_url": resolved_canonical_url,
        "seo_image_url": resolved_image_url,
        "seo_robots": robots,
        "seo_og_type": og_type,
        "seo_json_ld": serialize_json_ld(json_ld),
        "seo_site_name": "PaliGames",
        "seo_twitter_card": "summary_large_image",
    }
