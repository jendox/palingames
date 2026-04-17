from __future__ import annotations

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch
from django.templatetags.static import static
from django.urls import reverse

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.products.models import Product, ProductImage
from apps.products.pricing import format_price

from .models import Favorite

# Гостевая страница /favorites/: на десктопе до 3 карточек в ряд.
PUBLIC_FAVORITES_PER_PAGE = 9
# Вкладка «Избранное» в ЛК: на десктопе 2 карточки в ряд.
ACCOUNT_FAVORITES_PER_PAGE = 8

ACCOUNT_FAVORITES_SORT_OPTIONS = (
    ("title", "имя"),
    ("price_desc", "цена по убыванию"),
    ("price_asc", "цена по возрастанию"),
    ("newest", "новые игры"),
    ("oldest", "старые игры"),
)

_MOBILE_PAGINATION_COMPACT_LIMIT = 3
_MOBILE_PAGINATION_LEADING_WINDOW = 2

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


def _format_category_label(category) -> str:
    if category is None:
        return ""

    replacements = (
        ("ческие игры", "ческая игра"),
        ("тивные игры", "тивная игра"),
        ("ные игры", "ная игра"),
        ("ые игры", "ая игра"),
        ("ие игры", "ая игра"),
        ("ги", "га"),
    )

    title = category.title
    for source, target in replacements:
        if title.endswith(source):
            return title[: -len(source)] + target
    return title


def _build_favorite_card(product, *, cart_ids: set[int], purchased_ids: set[int]) -> dict:
    primary_kind = product.subtypes.first() or product.categories.first()
    primary_category = product.categories.first()
    primary_image = next(iter(product.images.all()), None)
    is_purchased = product.id in purchased_ids
    category_label = _format_category_label(primary_category) if primary_category else ""
    return {
        "id": product.id,
        "title": product.title,
        "url": product.get_absolute_url(),
        "price": format_price(product.price, product.currency),
        "kind": primary_kind.title if primary_kind else "",
        "category": category_label,
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


def _guest_session_index_by_product_id(session_product_ids: list[int]) -> dict[int, int]:
    return {pid: idx for idx, pid in enumerate(session_product_ids)}


def _sort_guest_favorite_products(
    products: list[Product],
    session_order_ids: list[int],
    sort_value: str,
) -> list[Product]:
    by_index = _guest_session_index_by_product_id(session_order_ids)

    if sort_value == "title":
        return sorted(products, key=lambda p: (p.title or "").lower())
    if sort_value == "price_asc":
        return sorted(products, key=lambda p: (p.price, (p.title or "").lower()))
    if sort_value == "price_desc":
        return sorted(products, key=lambda p: (-p.price, (p.title or "").lower()))
    if sort_value == "oldest":
        return sorted(products, key=lambda p: by_index.get(p.id, 0))
    # "newest" and fallback: later in session list = added more recently
    return sorted(products, key=lambda p: by_index.get(p.id, 0), reverse=True)


def get_favorites_page_context(request) -> dict:
    """Гостевая страница /favorites/: сортировка и пагинация как в каталоге."""
    product_ids = get_favorite_product_ids(request)

    empty_sort_options = [
        {"value": value, "label": label, "selected": value == "newest"}
        for value, label in ACCOUNT_FAVORITES_SORT_OPTIONS
    ]
    if not product_ids:
        return {
            "favorites_products": [],
            "favorites_count": 0,
            "favorites_page_obj": None,
            "favorites_sort_value": "newest",
            "favorites_sort_options": empty_sort_options,
            "favorites_mobile_pagination_items": [],
        }

    sort_value = request.GET.get("sort", "") or "newest"
    valid_sorts = {value for value, _ in ACCOUNT_FAVORITES_SORT_OPTIONS}
    if sort_value not in valid_sorts:
        sort_value = "newest"

    page_raw = request.GET.get("page") or 1
    try:
        page_number = int(page_raw)
    except (TypeError, ValueError):
        page_number = 1

    products_bulk = (
        Product.objects.filter(id__in=product_ids)
        .prefetch_related(
            "categories",
            "subtypes",
            Prefetch("images", queryset=ProductImage.objects.order_by("order")),
        )
        .in_bulk(product_ids)
    )
    ordered_products = [products_bulk[pid] for pid in product_ids if pid in products_bulk]

    ordered_products = _sort_guest_favorite_products(ordered_products, product_ids, sort_value)

    paginator = Paginator(ordered_products, PUBLIC_FAVORITES_PER_PAGE)
    page_obj = paginator.get_page(page_number)

    page_product_ids = [p.id for p in page_obj.object_list]
    cart_ids = set(get_cart_product_ids(request))
    purchased_ids = get_user_product_access_ids(request.user, product_ids=page_product_ids)

    cards = [
        _build_favorite_card(product, cart_ids=cart_ids, purchased_ids=purchased_ids)
        for product in page_obj.object_list
    ]

    sort_options = [
        {"value": value, "label": label, "selected": value == sort_value}
        for value, label in ACCOUNT_FAVORITES_SORT_OPTIONS
    ]

    return {
        "favorites_products": cards,
        "favorites_count": paginator.count,
        "favorites_page_obj": page_obj,
        "favorites_sort_value": sort_value,
        "favorites_sort_options": sort_options,
        "favorites_mobile_pagination_items": _build_mobile_pagination(page_obj),
    }


def _mobile_pagination_page_item(page_number: int, current_page: int) -> dict:
    return {
        "type": "page",
        "number": page_number,
        "current": page_number == current_page,
    }


def _build_mobile_pagination(page_obj) -> list[dict]:
    total_pages = page_obj.paginator.num_pages
    current_page = page_obj.number

    if total_pages <= 1:
        return []

    if total_pages <= _MOBILE_PAGINATION_COMPACT_LIMIT:
        return [
            _mobile_pagination_page_item(page_number, current_page)
            for page_number in range(1, total_pages + 1)
        ]

    if current_page <= _MOBILE_PAGINATION_LEADING_WINDOW:
        visible_pages = (1, 2, 3, total_pages)
        return [
            *[
                _mobile_pagination_page_item(page_number, current_page)
                for page_number in visible_pages[:-1]
            ],
            {"type": "ellipsis"},
            _mobile_pagination_page_item(visible_pages[-1], current_page),
        ]

    if current_page >= total_pages - 1:
        trailing_pages = (1, total_pages - 2, total_pages - 1, total_pages)
        return [
            _mobile_pagination_page_item(trailing_pages[0], current_page),
            {"type": "ellipsis"},
            *[
                _mobile_pagination_page_item(page_number, current_page)
                for page_number in trailing_pages[1:]
            ],
        ]

    return [
        _mobile_pagination_page_item(1, current_page),
        {"type": "ellipsis"},
        _mobile_pagination_page_item(current_page, current_page),
        {"type": "ellipsis"},
        _mobile_pagination_page_item(total_pages, current_page),
    ]


def _apply_favorite_sort(queryset, sort_value: str):
    sort_map = {
        "price_asc": ("product__price", "product__title"),
        "price_desc": ("-product__price", "product__title"),
        "title": ("product__title",),
        "newest": ("-created_at", "product__title"),
        "oldest": ("created_at", "product__title"),
    }
    return queryset.order_by(*sort_map.get(sort_value, sort_map["newest"]))


def get_account_favorites_context(request) -> dict:
    """Authenticated ЛК: избранное с сортировкой и пагинацией (как каталог)."""
    if not request.user.is_authenticated:
        return {
            "account_favorites_products": [],
            "account_favorites_page_obj": None,
            "account_favorites_count": 0,
            "account_favorites_sort_value": "",
            "account_favorites_sort_options": [],
            "account_favorites_mobile_pagination_items": [],
        }

    sort_value = request.GET.get("sort", "") or "newest"
    valid_sorts = {value for value, _ in ACCOUNT_FAVORITES_SORT_OPTIONS}
    if sort_value not in valid_sorts:
        sort_value = "newest"

    page_raw = request.GET.get("page") or 1
    try:
        page_number = int(page_raw)
    except (TypeError, ValueError):
        page_number = 1

    base_qs = (
        Favorite.objects.filter(user=request.user)
        .select_related("product")
        .prefetch_related(
            "product__categories",
            "product__subtypes",
            Prefetch("product__images", queryset=ProductImage.objects.order_by("order")),
        )
    )
    base_qs = _apply_favorite_sort(base_qs, sort_value)

    paginator = Paginator(base_qs, ACCOUNT_FAVORITES_PER_PAGE)
    page_obj = paginator.get_page(page_number)

    favorite_rows = list(page_obj.object_list)
    products = [row.product for row in favorite_rows]

    product_ids = [product.id for product in products]
    cart_ids = set(get_cart_product_ids(request))
    purchased_ids = get_user_product_access_ids(request.user, product_ids=product_ids)

    cards = [_build_favorite_card(product, cart_ids=cart_ids, purchased_ids=purchased_ids) for product in products]

    sort_options = [
        {"value": value, "label": label, "selected": value == sort_value}
        for value, label in ACCOUNT_FAVORITES_SORT_OPTIONS
    ]

    return {
        "account_favorites_products": cards,
        "account_favorites_page_obj": page_obj,
        "account_favorites_count": paginator.count,
        "account_favorites_sort_value": sort_value,
        "account_favorites_sort_options": sort_options,
        "account_favorites_mobile_pagination_items": _build_mobile_pagination(page_obj),
    }
