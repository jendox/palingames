from django.db.models import Prefetch
from django.templatetags.static import static
from django.urls import reverse

from apps.core.analytics_events import build_product_file_download_analytics_payload

from .models import (
    Product,
    ProductImage,
)
from .pricing import format_price, get_currency_code


def _get_active_product_file(product):
    return next((item for item in product.files.all() if item.is_active), None)


class CatalogListingMixin:
    def _base_products_queryset(self):
        return Product.objects.prefetch_related(
            "categories",
            "subtypes",
            "age_groups",
            "development_areas",
            "themes",
            "files",
            Prefetch("images", queryset=ProductImage.objects.order_by("order")),
        )

    def _format_category_label(self, category):
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

    def _build_product_card(
        self,
        product,
        selected_category=None,
        cart_product_ids=None,
        purchased_product_ids=None,
        favorite_product_ids=None,
    ):
        primary_kind = product.subtypes.first() or product.categories.first()
        primary_image = next(iter(product.images.all()), None)
        cart_ids = cart_product_ids or set()
        purchased_ids = purchased_product_ids or set()
        favorite_ids = favorite_product_ids or set()
        is_purchased = product.id in purchased_ids
        primary_category = selected_category or product.categories.first()
        active_file = _get_active_product_file(product) if is_purchased else None
        return {
            "id": product.id,
            "title": product.title,
            "url": product.get_absolute_url(),
            "price": format_price(product.price, product.currency),
            "price_value": float(product.price),
            "currency": get_currency_code(product.currency),
            "kind": primary_kind.title if primary_kind else "",
            "category": self._format_category_label(primary_category),
            "content": product.content,
            "rating": f"{product.average_rating:.1f}".replace(".", ","),
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
            "image_url": primary_image.image.url if primary_image else static("images/example-product-image-1.png"),
        }

    def _apply_sort(self, queryset, sort_value):
        sort_map = {
            "price_asc": ("price", "title"),
            "price_desc": ("-price", "title"),
            "title": ("title",),
            "newest": ("-created_at", "title"),
            "oldest": ("created_at", "title"),
        }
        return queryset.order_by(*sort_map.get(sort_value, sort_map["title"]))

    def _format_price_value(self, value):
        if value is None:
            return ""
        return f"{value:.2f}".replace(".", ",")

    mobile_pagination_compact_limit = 3
    mobile_pagination_leading_window = 2

    def _mobile_pagination_page_item(self, page_number, current_page):
        return {
            "type": "page",
            "number": page_number,
            "current": page_number == current_page,
        }

    def _build_mobile_pagination(self, page_obj):
        total_pages = page_obj.paginator.num_pages
        current_page = page_obj.number

        if total_pages <= 1:
            return []

        if total_pages <= self.mobile_pagination_compact_limit:
            return [
                self._mobile_pagination_page_item(page_number, current_page)
                for page_number in range(1, total_pages + 1)
            ]

        if current_page <= self.mobile_pagination_leading_window:
            visible_pages = (1, 2, 3, total_pages)
            return [
                *[
                    self._mobile_pagination_page_item(page_number, current_page)
                    for page_number in visible_pages[:-1]
                ],
                {"type": "ellipsis"},
                self._mobile_pagination_page_item(visible_pages[-1], current_page),
            ]

        if current_page >= total_pages - 1:
            trailing_pages = (1, total_pages - 2, total_pages - 1, total_pages)
            return [
                self._mobile_pagination_page_item(trailing_pages[0], current_page),
                {"type": "ellipsis"},
                *[
                    self._mobile_pagination_page_item(page_number, current_page)
                    for page_number in trailing_pages[1:]
                ],
            ]

        return [
            self._mobile_pagination_page_item(1, current_page),
            {"type": "ellipsis"},
            self._mobile_pagination_page_item(current_page, current_page),
            {"type": "ellipsis"},
            self._mobile_pagination_page_item(total_pages, current_page),
        ]
