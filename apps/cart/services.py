from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.templatetags.static import static

from apps.access.services import get_user_product_access_ids, has_user_product_access
from apps.products.models import Product
from apps.products.pricing import format_price

from .models import Cart, CartItem

SESSION_CART_KEY = "guest_cart_product_ids"


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


def _get_guest_cart_ids(request) -> list[int]:
    return _normalize_product_ids(request.session.get(SESSION_CART_KEY, []))


def _set_guest_cart_ids(request, product_ids: list[int]) -> None:
    request.session[SESSION_CART_KEY] = _normalize_product_ids(product_ids)
    request.session.modified = True


def _get_or_create_user_cart(user) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def get_cart_product_ids(request) -> list[int]:
    if request.user.is_authenticated:
        return list(
            CartItem.objects.filter(cart__user=request.user)
            .order_by("created_at")
            .values_list("product_id", flat=True),
        )
    return _get_guest_cart_ids(request)


@transaction.atomic
def toggle_cart_product(request, product_id: int) -> dict:
    if request.user.is_authenticated:
        if has_user_product_access(request.user, product_id):
            return {
                "in_cart": False,
                "already_purchased": True,
            }

        cart = _get_or_create_user_cart(request.user)
        item_qs = CartItem.objects.filter(cart=cart, product_id=product_id)
        if item_qs.exists():
            item_qs.delete()
            return {
                "in_cart": False,
                "already_purchased": False,
            }
        CartItem.objects.create(cart=cart, product_id=product_id)
        return {
            "in_cart": True,
            "already_purchased": False,
        }

    guest_ids = _get_guest_cart_ids(request)
    if product_id in guest_ids:
        guest_ids = [item_id for item_id in guest_ids if item_id != product_id]
        _set_guest_cart_ids(request, guest_ids)
        return {
            "in_cart": False,
            "already_purchased": False,
        }

    guest_ids.append(product_id)
    _set_guest_cart_ids(request, guest_ids)
    return {
        "in_cart": True,
        "already_purchased": False,
    }


@transaction.atomic
def remove_cart_product(request, product_id: int) -> None:
    if request.user.is_authenticated:
        CartItem.objects.filter(cart__user=request.user, product_id=product_id).delete()
        return

    guest_ids = [item_id for item_id in _get_guest_cart_ids(request) if item_id != product_id]
    _set_guest_cart_ids(request, guest_ids)


@transaction.atomic
def clear_cart(request) -> None:
    if request.user.is_authenticated:
        CartItem.objects.filter(cart__user=request.user).delete()
        return
    _set_guest_cart_ids(request, [])


@transaction.atomic
def merge_guest_cart_to_user(request, user) -> None:
    guest_ids = _get_guest_cart_ids(request)
    if not guest_ids:
        return

    cart = _get_or_create_user_cart(user)
    existing_ids = set(CartItem.objects.filter(cart=cart).values_list("product_id", flat=True))
    purchased_ids = get_user_product_access_ids(user, product_ids=guest_ids)

    missing_ids = [
        product_id for product_id in guest_ids if product_id not in existing_ids and product_id not in purchased_ids
    ]
    if missing_ids:
        CartItem.objects.bulk_create([CartItem(cart=cart, product_id=product_id) for product_id in missing_ids])

    _set_guest_cart_ids(request, [])


def _format_price(value: Decimal, currency: int | None) -> str:
    return format_price(value, currency)


def _build_cart_item(product: Product, selected_category_by_id: dict[int, str]) -> dict:
    category_label = selected_category_by_id.get(product.id) or (
        product.categories.first().title if product.categories.first() else ""
    )
    first_image = next(iter(product.images.all()), None)
    return {
        "product_id": product.id,
        "title": product.title,
        "kind": category_label,
        "price": _format_price(product.price, product.currency),
        "image_url": first_image.image.url if first_image else static("images/example-product-image-1.png"),
    }


def get_cart_page_context(request) -> dict:
    product_ids = get_cart_product_ids(request)
    if not product_ids:
        return {
            "cart_items": [],
            "cart_total": _format_price(Decimal("0.00"), None),
            "cart_count": 0,
            "cart_product_ids": [],
        }

    products = (
        Product.objects.filter(id__in=product_ids)
        .prefetch_related("categories", "images")
        .in_bulk(product_ids)
    )

    ordered_products = [products[product_id] for product_id in product_ids if product_id in products]
    selected_category_by_id = {}
    for product in ordered_products:
        first_category = product.categories.first()
        selected_category_by_id[product.id] = first_category.title if first_category else ""
    cart_items = [_build_cart_item(product, selected_category_by_id) for product in ordered_products]
    total = sum((product.price for product in ordered_products), Decimal("0.00"))
    cart_currency = ordered_products[0].currency if ordered_products else None

    return {
        "cart_items": cart_items,
        "cart_total": _format_price(total, cart_currency),
        "cart_count": len(ordered_products),
        "cart_product_ids": [product.id for product in ordered_products],
    }
