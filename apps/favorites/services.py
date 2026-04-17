from __future__ import annotations

from django.db import transaction
from django.db.models import Prefetch
from django.templatetags.static import static
from django.urls import reverse

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.products.models import Product, ProductImage
from apps.products.pricing import format_price

from .models import Favorite

SESSION_FAVORITES_KEY = "guest_favorite_product_ids"


def _normalize_product_ids(raw_ids: list) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_id in raw_ids:
        try:
            product_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if product_id <= 0 or product_id in seen:
            continue
        seen.add(product_id)
        normalized.append(product_id)
    return normalized


def _get_guest_favorite_ids(request) -> list[int]:
    return _normalize_product_ids(request.session.get(SESSION_FAVORITES_KEY, []))


def _set_guest_favorite_ids(request, product_ids: list[int]) -> None:
    request.session[SESSION_FAVORITES_KEY] = _normalize_product_ids(product_ids)
    request.session.modified = True


def get_favorite_product_ids(request) -> list[int]:
    if request.user.is_authenticated:
        return list(
            Favorite.objects.filter(user=request.user)
            .order_by("-created_at")
            .values_list("product_id", flat=True),
        )
    return _get_guest_favorite_ids(request)


@transaction.atomic
def toggle_favorite_product(request, product_id: int) -> dict:
    if request.user.is_authenticated:
        item_qs = Favorite.objects.filter(user=request.user, product_id=product_id)
        if item_qs.exists():
            item_qs.delete()
            return {"is_favorited": False}
        Favorite.objects.create(user=request.user, product_id=product_id)
        return {"is_favorited": True}

    guest_ids = _get_guest_favorite_ids(request)
    if product_id in guest_ids:
        guest_ids = [item_id for item_id in guest_ids if item_id != product_id]
        _set_guest_favorite_ids(request, guest_ids)
        return {"is_favorited": False}

    guest_ids.append(product_id)
    _set_guest_favorite_ids(request, guest_ids)
    return {"is_favorited": True}


@transaction.atomic
def remove_favorite_product(request, product_id: int) -> None:
    if request.user.is_authenticated:
        Favorite.objects.filter(user=request.user, product_id=product_id).delete()
        return

    guest_ids = [item_id for item_id in _get_guest_favorite_ids(request) if item_id != product_id]
    _set_guest_favorite_ids(request, guest_ids)


@transaction.atomic
def clear_favorites(request) -> None:
    if request.user.is_authenticated:
        Favorite.objects.filter(user=request.user).delete()
        return
    _set_guest_favorite_ids(request, [])


@transaction.atomic
def merge_guest_favorites_to_user(request, user) -> None:
    guest_ids = _get_guest_favorite_ids(request)
    if not guest_ids:
        return

    existing_ids = set(
        Favorite.objects.filter(user=user, product_id__in=guest_ids).values_list("product_id", flat=True),
    )
    missing_ids = [product_id for product_id in guest_ids if product_id not in existing_ids]
    if missing_ids:
        Favorite.objects.bulk_create(
            [Favorite(user=user, product_id=product_id) for product_id in missing_ids],
            ignore_conflicts=True,
        )

    _set_guest_favorite_ids(request, [])


def _build_favorite_card(product, *, cart_ids: set[int], purchased_ids: set[int]) -> dict:
    primary_kind = product.subtypes.first() or product.categories.first()
    primary_category = product.categories.first()
    primary_image = next(iter(product.images.all()), None)
    is_purchased = product.id in purchased_ids
    return {
        "id": product.id,
        "title": product.title,
        "url": product.get_absolute_url(),
        "price": format_price(product.price, product.currency),
        "kind": primary_kind.title if primary_kind else "",
        "category": primary_category.title if primary_category else "",
        "content": product.content,
        "rating": f"{product.average_rating:.1f}".replace(".", ","),
        "is_favorited": True,
        "is_in_cart": product.id in cart_ids and not is_purchased,
        "is_purchased": is_purchased,
        "download_url": (
            reverse("product-download", kwargs={"product_id": product.id}) if is_purchased else ""
        ),
        "image_url": primary_image.image.url if primary_image else static("images/example-product-image-1.png"),
    }


def get_favorites_page_context(request) -> dict:
    product_ids = get_favorite_product_ids(request)
    if not product_ids:
        return {
            "favorites_products": [],
            "favorites_count": 0,
        }

    products = (
        Product.objects.filter(id__in=product_ids)
        .prefetch_related(
            "categories",
            "subtypes",
            Prefetch("images", queryset=ProductImage.objects.order_by("order")),
        )
        .in_bulk(product_ids)
    )
    ordered_products = [products[product_id] for product_id in product_ids if product_id in products]

    cart_ids = set(get_cart_product_ids(request))
    purchased_ids = get_user_product_access_ids(
        request.user,
        product_ids=[product.id for product in ordered_products],
    )

    cards = [
        _build_favorite_card(product, cart_ids=cart_ids, purchased_ids=purchased_ids)
        for product in ordered_products
    ]

    return {
        "favorites_products": cards,
        "favorites_count": len(cards),
    }
