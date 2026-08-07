from __future__ import annotations

from django.db.models import Count, F, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.products.models import ProductCollection, ProductImage


def get_public_collections_queryset():
    now = timezone.now()
    return (
        ProductCollection.objects.filter(is_published=True)
        .filter(Q(publish_starts_at__isnull=True) | Q(publish_starts_at__lte=now))
        .filter(Q(publish_ends_at__isnull=True) | Q(publish_ends_at__gte=now))
        .annotate(
            visible_count=Count(
                "collection_products",
                filter=Q(collection_products__product__files__is_active=True),
                distinct=True,
            ),
        )
        .filter(visible_count__gte=F("min_products_to_publish"))
        .order_by("sort_order", "title")
    )


def get_collection_products_queryset(collection: ProductCollection):
    return collection.visible_products_queryset().prefetch_related(
        "categories",
        "subtypes",
        "age_groups",
        "development_areas",
        "themes",
        "files",
        Prefetch("images", queryset=ProductImage.objects.order_by("order")),
    )


def get_public_collection_or_404(slug: str) -> ProductCollection:
    collection = get_object_or_404(
        get_public_collections_queryset(),
        slug=slug,
    )
    if collection.visible_products_count() < collection.min_products_to_publish:
        raise Http404
    if not collection.is_within_publish_window():
        raise Http404
    return collection
