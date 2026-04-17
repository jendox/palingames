"""Поиск по каталогу: PostgreSQL pg_trgm; fallback icontains для не-Postgres (тесты)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import connection
from django.db.models import Q

if TYPE_CHECKING:
    from django.db.models import QuerySet

MIN_QUERY_LEN = 2
TRIGRAM_THRESHOLD = 0.22


def normalize_catalog_search_q(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    return s.replace("ё", "е").replace("Ё", "Е")


def apply_product_search(queryset: QuerySet, q: str) -> tuple[QuerySet, bool, bool]:
    """Фильтрует queryset по поисковой строке.

    Возвращает (queryset, search_active, order_by_similarity).
    search_active=False если строка короче MIN_QUERY_LEN.
    order_by_similarity=True если можно сортировать по TrigramSimilarity (PostgreSQL).
    """
    qn = normalize_catalog_search_q(q)
    if len(qn) < MIN_QUERY_LEN:
        return queryset, False, False

    if connection.vendor != "postgresql":
        filtered = queryset.filter(title__icontains=qn).distinct()
        return filtered, True, False

    from django.contrib.postgres.search import TrigramSimilarity

    annotated = queryset.annotate(_search_sim=TrigramSimilarity("title", qn))
    filtered = annotated.filter(Q(title__icontains=qn) | Q(_search_sim__gte=TRIGRAM_THRESHOLD)).distinct()
    return filtered, True, True


def order_catalog_queryset(
    queryset: QuerySet,
    *,
    sort_value: str,
    search_active: bool,
    order_by_similarity: bool,
) -> QuerySet:
    """Порядок выдачи: при поиске без явной сортировки — по релевантности."""
    sort_value = (sort_value or "").strip()
    if search_active and order_by_similarity and not sort_value:
        return queryset.order_by("-_search_sim", "title")
    return _apply_sort_map(queryset, sort_value or "title")


def _apply_sort_map(queryset: QuerySet, sort_value: str) -> QuerySet:
    sort_map = {
        "price_asc": ("price", "title"),
        "price_desc": ("-price", "title"),
        "title": ("title",),
        "newest": ("-created_at", "title"),
        "oldest": ("created_at", "title"),
    }
    return queryset.order_by(*sort_map.get(sort_value, sort_map["title"]))


def suggest_product_hits(q: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Подсказки для API: заголовок и URL товара."""
    from .models import Product

    qs = Product.objects.all()
    qs, active, order_by_sim = apply_product_search(qs, q)
    if not active:
        return []
    if order_by_sim:
        qs = qs.order_by("-_search_sim", "title")
    else:
        qs = qs.order_by("title")
    return [
        {
            "title": p.title,
            "url": p.get_absolute_url(),
        }
        for p in qs[:limit]
    ]
