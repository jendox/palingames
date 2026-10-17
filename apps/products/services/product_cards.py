from __future__ import annotations

from django.urls import reverse

from apps.core.analytics_events import build_product_file_download_analytics_payload
from apps.products.listing_images import build_listing_image_fields
from apps.products.pricing import format_price, get_currency_code
from apps.products.services.reviews import get_product_rating_summaries

_CATEGORY_LABEL_REPLACEMENTS = (
    ("ческие игры", "ческая игра"),
    ("тивные игры", "тивная игра"),
    ("ные игры", "ная игра"),
    ("ые игры", "ая игра"),
    ("ие игры", "ая игра"),
    ("ги", "га"),
)


def format_category_label(category) -> str:
    if category is None:
        return ""

    title = category.title
    for source, target in _CATEGORY_LABEL_REPLACEMENTS:
        if title.endswith(source):
            return title[: -len(source)] + target
    return title


def get_active_product_file(product):
    return next((item for item in product.files.all() if item.is_active), None)


def build_product_card(
    product,
    *,
    selected_category=None,
    cart_product_ids=None,
    purchased_product_ids=None,
    favorite_product_ids=None,
    rating_summaries=None,
) -> dict:
    primary_kind = product.subtypes.first() or product.categories.first()
    primary_image = next(iter(product.images.all()), None)
    cart_ids = cart_product_ids or set()
    purchased_ids = purchased_product_ids or set()
    favorite_ids = favorite_product_ids or set()
    is_purchased = product.id in purchased_ids
    primary_category = selected_category or product.categories.first()
    active_file = get_active_product_file(product) if is_purchased else None
    summary = (rating_summaries or {}).get(product.id)
    has_rating = summary is not None and summary.count > 0
    return {
        "id": product.id,
        "title": product.title,
        "url": product.get_absolute_url(),
        "price": format_price(product.price, product.currency),
        "price_value": float(product.price),
        "currency": get_currency_code(product.currency),
        "kind": primary_kind.title if primary_kind else "",
        "category": format_category_label(primary_category),
        "content": product.content,
        "rating": f"{summary.average:.1f}".replace(".", ",") if has_rating else "",
        "reviews_count": summary.count if summary else 0,
        "has_rating": has_rating,
        "is_favorited": product.id in favorite_ids,
        "is_in_cart": product.id in cart_ids and not is_purchased,
        "is_purchased": is_purchased,
        "download_url": reverse("product-download", kwargs={"product_id": product.id}) if is_purchased else "",
        "download_analytics": (
            build_product_file_download_analytics_payload(
                product=product,
                product_file=active_file,
                primary_category=primary_category,
                primary_kind=primary_kind,
            )
            if is_purchased
            else None
        ),
        **build_listing_image_fields(primary_image),
    }


def build_product_cards(request, products, *, selected_category=None) -> list[dict]:
    from apps.access.services import get_user_product_access_ids
    from apps.cart.services import get_cart_product_ids
    from apps.favorites.services import get_favorite_product_ids

    products = list(products)
    if not products:
        return []
    product_ids = [product.id for product in products]
    cart_product_ids = set(get_cart_product_ids(request))
    favorite_product_ids = set(get_favorite_product_ids(request))
    purchased_product_ids = get_user_product_access_ids(request.user, product_ids=product_ids)
    rating_summaries = get_product_rating_summaries(product_ids)
    return [
        build_product_card(
            product,
            selected_category=selected_category,
            cart_product_ids=cart_product_ids,
            purchased_product_ids=purchased_product_ids,
            favorite_product_ids=favorite_product_ids,
            rating_summaries=rating_summaries,
        )
        for product in products
    ]
