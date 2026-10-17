from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Prefetch
from django.utils import timezone

from apps.products.models import Product, ProductImage, SubType

logger = logging.getLogger("apps.products")

POPULAR_ALGORITHM_VERSION = 1

_INT_PATTERN = re.compile(r"[+-]?[0-9]+")


@dataclass(frozen=True)
class PopularSettings:
    limit: int
    window_days: int
    min_orders: int
    cache_ttl: int


_SPECS = {  # attr: (settings name, default, min, max)
    "limit": ("HOME_POPULAR_PRODUCTS_LIMIT", 6, 1, 24),
    "window_days": ("HOME_POPULAR_PRODUCTS_WINDOW_DAYS", 30, 1, 365),
    # Tier 2 holds products with 1..MIN_ORDERS-1 orders, so MIN_ORDERS must be >= 2.
    "min_orders": ("HOME_POPULAR_PRODUCTS_MIN_ORDERS", 2, 2, 100),
    "cache_ttl": ("HOME_POPULAR_PRODUCTS_CACHE_TTL", 3600, 60, 86400),
}
_warned: set[tuple[str, str]] = set()


def _reset_warnings() -> None:
    _warned.clear()


def _warn_invalid_setting(name: str, raw, default: int, lo: int, hi: int) -> None:
    warn_key = (name, repr(raw))
    if warn_key in _warned:
        return
    _warned.add(warn_key)
    logger.warning(
        "home_popular.invalid_setting name=%s value=%r default=%s allowed=%s..%s",
        name,
        raw,
        default,
        lo,
        hi,
    )


def _parse_int_setting(name: str, default: int, lo: int, hi: int) -> int:
    raw = getattr(settings, name, None)
    if raw is None:
        return default

    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return default
        value = int(stripped) if _INT_PATTERN.fullmatch(stripped) else None
    else:
        value = None

    if value is None or not lo <= value <= hi:
        _warn_invalid_setting(name, raw, default, lo, hi)
        return default
    return value


def get_popular_settings() -> PopularSettings:
    return PopularSettings(
        **{attr: _parse_int_setting(name, default, lo, hi) for attr, (name, default, lo, hi) in _SPECS.items()},
    )


def get_popular_cache_key(cfg: PopularSettings) -> str:
    return f"home_popular:v{POPULAR_ALGORITHM_VERSION}:{cfg.window_days}:{cfg.limit}:{cfg.min_orders}"


def _ranking_key(row: dict) -> tuple:
    return row["paid_orders"], row["last_paid_at"], row["product_id"]


def compute_popular_product_ids(cfg: PopularSettings) -> list[int]:
    from apps.orders.models import Order, OrderItem

    since = timezone.now() - timedelta(days=cfg.window_days)
    rows = list(
        OrderItem.objects.filter(
            order__status=Order.OrderStatus.PAID,
            order__paid_at__gte=since,
            product__is_published=True,
        )
        .order_by()
        .values("product_id")
        .annotate(
            paid_orders=Count("order_id", distinct=True),
            last_paid_at=Max("order__paid_at"),
        ),
    )

    # Descending by paid_orders, then freshest last_paid_at, then larger product_id.
    tier_1 = sorted((row for row in rows if row["paid_orders"] >= cfg.min_orders), key=_ranking_key, reverse=True)
    tier_2 = sorted(
        (row for row in rows if 1 <= row["paid_orders"] <= cfg.min_orders - 1),
        key=_ranking_key,
        reverse=True,
    )
    product_ids = [row["product_id"] for row in (*tier_1, *tier_2)][: cfg.limit]

    remaining = cfg.limit - len(product_ids)
    if remaining > 0:
        product_ids.extend(
            Product.objects.published()
            .exclude(id__in=product_ids)
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)[:remaining],
        )
    return product_ids


def get_popular_product_ids(cfg: PopularSettings | None = None) -> list[int]:
    cfg = cfg or get_popular_settings()
    key = get_popular_cache_key(cfg)
    try:
        cached = cache.get(key)
    except Exception as exc:  # noqa: BLE001 - cache outage must not break the home page
        logger.warning("home_popular.cache_unavailable op=get key=%s error=%s", key, type(exc).__name__)
        return compute_popular_product_ids(cfg)
    if cached is not None:
        return cached

    product_ids = compute_popular_product_ids(cfg)
    try:
        cache.set(key, product_ids, cfg.cache_ttl)
    except Exception as exc:  # noqa: BLE001 - cache outage must not break the home page
        logger.warning("home_popular.cache_unavailable op=set key=%s error=%s", key, type(exc).__name__)
    return product_ids


def get_popular_products(cfg: PopularSettings | None = None) -> list[Product]:
    product_ids = get_popular_product_ids(cfg)
    if not product_ids:
        return []
    products = Product.objects.published().filter(id__in=product_ids).prefetch_related(
        "categories",
        # SubType has no Meta.ordering; an ordered prefetch lets subtypes.first() read the cache (no N+1).
        Prefetch("subtypes", queryset=SubType.objects.order_by("id")),
        "files",
        Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
    )
    products_by_id = {product.id: product for product in products}
    return [products_by_id[product_id] for product_id in product_ids if product_id in products_by_id]
