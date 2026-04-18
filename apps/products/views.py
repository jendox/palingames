import logging
from enum import StrEnum

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Prefetch
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.core.logging import log_event
from apps.core.metrics import inc_product_download_failed, inc_product_download_redirect
from apps.core.rate_limits import RateLimitScope, check_rate_limit
from apps.favorites.services import get_favorite_product_ids

from .models import AgeGroupTag, Category, DevelopmentAreaTag, Product, ProductImage, Review, SubType, Theme
from .pricing import format_price
from .search import (
    MIN_QUERY_LEN,
    apply_product_search,
    normalize_catalog_search_q,
    order_catalog_queryset,
    suggest_product_hits,
)
from .services.s3 import ProductFileDownloadUrlError, generate_presigned_download_url

logger = logging.getLogger("apps.products")

PRODUCT_DOWNLOAD_RATE_LIMIT_MESSAGE = "Слишком много запросов на скачивание. Попробуйте позже."


class CatalogView(TemplateView):
    template_name = "pages/catalog.html"
    htmx_results_template_name = "pages/catalog/desktop/results_panel.html"
    htmx_mobile_template_name = "pages/catalog/mobile/product_listing.html"
    card_styles = (
        {
            "background_class": "bg-[var(--color-mint)]",
            "text_class": "text-[var(--color-turquoise)]",
        },
        {
            "background_class": "bg-[var(--color-lilac)]",
            "text_class": "text-[var(--color-purple)]",
        },
    )
    sort_options = (
        ("title", "имя"),
        ("price_desc", "цена по убыванию"),
        ("price_asc", "цена по возрастанию"),
        ("newest", "новые игры"),
        ("oldest", "старые игры"),
    )
    mobile_pagination_compact_limit = 3
    mobile_pagination_leading_window = 2

    def _base_products_queryset(self):
        return Product.objects.prefetch_related(
            "categories",
            "subtypes",
            "age_groups",
            "development_areas",
            "themes",
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
        return {
            "id": product.id,
            "title": product.title,
            "url": product.get_absolute_url(),
            "price": format_price(product.price, product.currency),
            "kind": primary_kind.title if primary_kind else "",
            "category": self._format_category_label(selected_category or product.categories.first()),
            "content": product.content,
            "rating": f"{product.average_rating:.1f}".replace(".", ","),
            "is_favorited": product.id in favorite_ids,
            "is_in_cart": product.id in cart_ids and not is_purchased,
            "is_purchased": is_purchased,
            "download_url": reverse("product-download", kwargs={"product_id": product.id}) if is_purchased else "",
            "image_url": primary_image.image.url if primary_image else static("images/example-product-image-1.png"),
        }

    def _format_price_value(self, value):
        if value is None:
            return ""
        return f"{value:.2f}".replace(".", ",")

    def _selected_values(self, key):
        return self.request.GET.getlist(key)

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

    def _apply_filters(self, queryset):
        many_to_many_filters = (
            ("subtype", "subtypes__id__in"),
            ("age", "age_groups__id__in"),
            ("area", "development_areas__id__in"),
            ("theme", "themes__id__in"),
        )

        for param_name, lookup in many_to_many_filters:
            selected_values = self._selected_values(param_name)
            if selected_values:
                queryset = queryset.filter(**{lookup: selected_values})

        price_from = self.request.GET.get("price_from")
        price_to = self.request.GET.get("price_to")

        if price_from:
            try:
                queryset = queryset.filter(price__gte=price_from.replace(",", "."))
            except ValueError:
                pass
        if price_to:
            try:
                queryset = queryset.filter(price__lte=price_to.replace(",", "."))
            except ValueError:
                pass

        return queryset.distinct()

    def _apply_sort(self, queryset, sort_value):
        sort_map = {
            "price_asc": ("price", "title"),
            "price_desc": ("-price", "title"),
            "title": ("title",),
            "newest": ("-created_at", "title"),
            "oldest": ("created_at", "title"),
        }
        return queryset.order_by(*sort_map.get(sort_value, sort_map["title"]))

    def _filter_option_queryset(self, model, field_name, queryset, **filters):
        return (
            model.objects.filter(**filters)
            .filter(**{f"{field_name}__in": queryset})
            .annotate(product_count=Count(field_name, distinct=True))
            .order_by("title" if hasattr(model, "title") else "value")
            .distinct()
        )

    def _build_catalog_filters_context(
        self,
        selected_category,
        category_queryset,
        selected_subtypes,
        selected_ages,
        selected_areas,
        selected_themes,
    ):
        subtype_filters = {}
        if selected_category is not None:
            subtype_filters["category"] = selected_category
        subtype_options = self._filter_option_queryset(
            SubType,
            "products",
            category_queryset,
            **subtype_filters,
        )
        age_options = self._filter_option_queryset(AgeGroupTag, "products", category_queryset)
        area_options = self._filter_option_queryset(DevelopmentAreaTag, "products", category_queryset)
        theme_options = self._filter_option_queryset(Theme, "products", category_queryset)

        catalog_filters = {
            "subtypes": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_subtypes,
                }
                for option in subtype_options
            ],
            "ages": [
                {
                    "id": option.pk,
                    "label": option.value,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_ages,
                }
                for option in age_options
            ],
            "areas": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_areas,
                }
                for option in area_options
            ],
            "themes": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_themes,
                }
                for option in theme_options
            ],
        }

        catalog_quick_filters = [
            {
                "id": option["id"],
                "label": option["label"],
                "selected": option["selected"],
            }
            for option in catalog_filters["areas"][:5]
        ]

        return catalog_filters, catalog_quick_filters

    def _build_products_mode_context(self, selected_category, *, search_q: str = ""):  # noqa: PLR0914
        sort_value = self.request.GET.get("sort", "").strip()
        page_number = self.request.GET.get("page") or 1

        scope_queryset = self._base_products_queryset()
        if selected_category is not None:
            scope_queryset = scope_queryset.filter(categories=selected_category)

        scope_queryset, search_active, order_by_sim = apply_product_search(scope_queryset, search_q)
        price_bounds = scope_queryset.aggregate(min_price=Min("price"), max_price=Max("price"))
        filtered_queryset = self._apply_filters(scope_queryset)
        sorted_queryset = order_catalog_queryset(
            filtered_queryset,
            sort_value=sort_value,
            search_active=search_active,
            order_by_similarity=order_by_sim,
        )

        selected_subtypes = self._selected_values("subtype")
        selected_ages = self._selected_values("age")
        selected_areas = self._selected_values("area")
        selected_themes = self._selected_values("theme")

        catalog_filters, catalog_quick_filters = self._build_catalog_filters_context(
            selected_category,
            scope_queryset,
            selected_subtypes,
            selected_ages,
            selected_areas,
            selected_themes,
        )

        cart_product_ids = set(get_cart_product_ids(self.request))
        favorite_product_ids = set(get_favorite_product_ids(self.request))
        purchased_product_ids = get_user_product_access_ids(
            self.request.user,
            product_ids=list(sorted_queryset.values_list("id", flat=True)),
        )

        desktop_paginator = Paginator(sorted_queryset, 9)
        desktop_page_obj = desktop_paginator.get_page(page_number)

        mobile_paginator = Paginator(sorted_queryset, 8)
        mobile_page_obj = mobile_paginator.get_page(page_number)

        return {
            "catalog_products": [
                self._build_product_card(
                    product,
                    selected_category=selected_category,
                    cart_product_ids=cart_product_ids,
                    purchased_product_ids=purchased_product_ids,
                    favorite_product_ids=favorite_product_ids,
                )
                for product in desktop_page_obj.object_list
            ],
            "catalog_products_count": desktop_paginator.count,
            "catalog_page_obj": desktop_page_obj,
            "catalog_pagination": {
                "current": desktop_page_obj.number,
                "total": desktop_paginator.num_pages,
                "has_previous": desktop_page_obj.has_previous(),
                "has_next": desktop_page_obj.has_next(),
                "previous_page": desktop_page_obj.previous_page_number() if desktop_page_obj.has_previous() else None,
                "next_page": desktop_page_obj.next_page_number() if desktop_page_obj.has_next() else None,
                "pages": list(desktop_paginator.page_range),
            },
            "catalog_mobile_products": [
                self._build_product_card(
                    product,
                    selected_category=selected_category,
                    cart_product_ids=cart_product_ids,
                    purchased_product_ids=purchased_product_ids,
                    favorite_product_ids=favorite_product_ids,
                )
                for product in mobile_page_obj.object_list
            ],
            "catalog_mobile_products_count": mobile_paginator.count,
            "catalog_mobile_page_obj": mobile_page_obj,
            "catalog_mobile_pagination_items": self._build_mobile_pagination(mobile_page_obj),
            "catalog_sort_value": sort_value,
            "catalog_sort_options": [
                {"value": value, "label": label, "selected": value == sort_value}
                for value, label in self.sort_options
            ],
            "catalog_selected_filters": {
                "subtypes": selected_subtypes,
                "ages": selected_ages,
                "areas": selected_areas,
                "themes": selected_themes,
            },
            "catalog_price_bounds": {
                "min": price_bounds["min_price"],
                "max": price_bounds["max_price"],
            },
            "catalog_selected_price_from": self.request.GET.get("price_from", ""),
            "catalog_selected_price_to": self.request.GET.get("price_to", ""),
            "catalog_price_bounds_display": {
                "min": self._format_price_value(price_bounds["min_price"]),
                "max": self._format_price_value(price_bounds["max_price"]),
            },
            "catalog_filters": catalog_filters,
            "catalog_quick_filters": catalog_quick_filters,
            "catalog_search_q": search_q if search_active else "",
            "catalog_search_active": search_active,
        }

    def get_template_names(self):
        category_slug = self.request.GET.get("category")
        q_normalized = normalize_catalog_search_q(self.request.GET.get("q", ""))
        hx_ok = bool(category_slug) or len(q_normalized) >= MIN_QUERY_LEN
        if self.request.headers.get("HX-Request") == "true" and hx_ok:
            if self.request.headers.get("HX-Target") == "catalog-mobile-listing-root":
                return [self.htmx_mobile_template_name]
            return [self.htmx_results_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = []
        for index, category in enumerate(Category.objects.order_by("id")):
            style = self.card_styles[index % len(self.card_styles)]
            categories.append(
                {
                    "title": category.title,
                    "slug": category.slug,
                    "url": f"/catalog/?category={category.slug}",
                    "image_url": static(f"images/catalog/{category.slug}.svg"),
                    **style,
                },
            )
        context["catalog_categories"] = categories

        selected_category = None
        category_slug = self.request.GET.get("category")
        if category_slug:
            selected_category = Category.objects.filter(slug=category_slug).first()

        search_q = normalize_catalog_search_q(self.request.GET.get("q", ""))
        search_ok = len(search_q) >= MIN_QUERY_LEN

        context["selected_category"] = selected_category
        products_mode = bool(selected_category) or search_ok
        context["catalog_mode"] = "products" if products_mode else "categories"

        breadcrumbs = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Каталог", "url": reverse("catalog") if (selected_category or search_ok) else None},
        ]
        if selected_category:
            breadcrumbs.append({"title": selected_category.title})
        elif search_ok:
            breadcrumbs.append({"title": f'{_("Поиск")}: «{search_q}»'})
        context["breadcrumbs"] = breadcrumbs

        if not products_mode:
            return context

        context.update(self._build_products_mode_context(selected_category, search_q=search_q))
        return context


class CatalogSearchSuggestView(View):
    """AJAX-подсказки по названию товара (trigram / icontains)."""

    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "")
        hits = suggest_product_hits(q, limit=10)
        return JsonResponse({"results": hits})


class AlphabetNavigatorView(CatalogView):
    template_name = "pages/alphabet_navigator.html"
    htmx_results_template_name = "pages/alphabet_navigator/desktop/results_panel.html"
    htmx_mobile_template_name = "pages/alphabet_navigator/mobile/product_listing.html"
    alphabet_letters = (
        "А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К", "Л", "М", "Н",
        "О", "П", "Р", "С", "Т", "У", "Ф", "Х", "Ц", "Ч", "Ш", "Щ", "Э", "Ю", "Я",
    )
    alphabet_mobile_letter_rows = (
        ("А", "Б", "В", "Г", "Д", "Е", "Ж"),
        ("З", "И", "К", "Л", "М", "Н", "О", "П"),
        ("Р", "С", "Т", "У", "Ф", "Х", "Ц", "Ч", "Ш"),
        ("Щ", "Э", "Ю", "Я"),
    )
    alphabet_desktop_letter_rows = (
        ("А", "Б", "В", "Г", "Д", "Е", "Ж", "З", "И", "К", "Л", "М", "Н", "О", "П", "Р", "С", "Т"),
        ("У", "Ф", "Х", "Ц", "Ч", "Ш", "Щ", "Э", "Ю", "Я"),
    )

    def _selected_letter(self):
        letter = (self.request.GET.get("letter") or "А").strip().upper()
        return letter if letter in self.alphabet_letters else "А"

    def _build_alphabet_letters_context(self, selected_letter):
        return [
            {
                "value": letter,
                "selected": letter == selected_letter,
            }
            for letter in self.alphabet_letters
        ]

    def _build_alphabet_mobile_letter_rows_context(self, selected_letter):
        return [
            [
                {
                    "value": letter,
                    "selected": letter == selected_letter,
                }
                for letter in row
            ]
            for row in self.alphabet_mobile_letter_rows
        ]

    def _build_alphabet_desktop_letter_rows_context(self, selected_letter):
        return [
            [
                {
                    "value": letter,
                    "selected": letter == selected_letter,
                }
                for letter in row
            ]
            for row in self.alphabet_desktop_letter_rows
        ]

    def _build_alphabet_filters_context(
        self,
        queryset,
        selected_subtypes,
        selected_ages,
        selected_areas,
        selected_themes,
    ):
        subtype_options = self._filter_option_queryset(SubType, "products", queryset)
        age_options = self._filter_option_queryset(AgeGroupTag, "products", queryset)
        area_options = self._filter_option_queryset(DevelopmentAreaTag, "products", queryset)
        theme_options = self._filter_option_queryset(Theme, "products", queryset)

        return {
            "subtypes": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_subtypes,
                }
                for option in subtype_options
            ],
            "ages": [
                {
                    "id": option.pk,
                    "label": option.value,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_ages,
                }
                for option in age_options
            ],
            "areas": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_areas,
                }
                for option in area_options
            ],
            "themes": [
                {
                    "id": option.pk,
                    "label": option.title,
                    "count": option.product_count,
                    "selected": str(option.pk) in selected_themes,
                }
                for option in theme_options
            ],
        }

    def _build_products_mode_context(self, selected_category=None):  # noqa: PLR0914
        sort_value = self.request.GET.get("sort", "")
        page_number = self.request.GET.get("page") or 1
        selected_letter = self._selected_letter()

        base_queryset = self._base_products_queryset().filter(title__istartswith=selected_letter)
        price_bounds = base_queryset.aggregate(min_price=Min("price"), max_price=Max("price"))
        filtered_queryset = self._apply_filters(base_queryset)
        sorted_queryset = self._apply_sort(filtered_queryset, sort_value)
        cart_product_ids = set(get_cart_product_ids(self.request))
        favorite_product_ids = set(get_favorite_product_ids(self.request))
        purchased_product_ids = get_user_product_access_ids(
            self.request.user,
            product_ids=list(sorted_queryset.values_list("id", flat=True)),
        )

        selected_subtypes = self._selected_values("subtype")
        selected_ages = self._selected_values("age")
        selected_areas = self._selected_values("area")
        selected_themes = self._selected_values("theme")

        alphabet_filters = self._build_alphabet_filters_context(
            base_queryset,
            selected_subtypes,
            selected_ages,
            selected_areas,
            selected_themes,
        )

        desktop_paginator = Paginator(sorted_queryset, 9)
        desktop_page_obj = desktop_paginator.get_page(page_number)

        mobile_paginator = Paginator(sorted_queryset, 8)
        mobile_page_obj = mobile_paginator.get_page(page_number)

        return {
            "alphabet_letters": self._build_alphabet_letters_context(selected_letter),
            "alphabet_desktop_letter_rows": self._build_alphabet_desktop_letter_rows_context(selected_letter),
            "alphabet_mobile_letter_rows": self._build_alphabet_mobile_letter_rows_context(selected_letter),
            "alphabet_selected_letter": selected_letter,
            "alphabet_products": [
                self._build_product_card(
                    product,
                    cart_product_ids=cart_product_ids,
                    purchased_product_ids=purchased_product_ids,
                    favorite_product_ids=favorite_product_ids,
                )
                for product in desktop_page_obj.object_list
            ],
            "alphabet_products_count": desktop_paginator.count,
            "alphabet_page_obj": desktop_page_obj,
            "alphabet_mobile_products": [
                self._build_product_card(
                    product,
                    cart_product_ids=cart_product_ids,
                    purchased_product_ids=purchased_product_ids,
                    favorite_product_ids=favorite_product_ids,
                )
                for product in mobile_page_obj.object_list
            ],
            "alphabet_mobile_products_count": mobile_paginator.count,
            "alphabet_mobile_page_obj": mobile_page_obj,
            "alphabet_mobile_pagination_items": self._build_mobile_pagination(mobile_page_obj),
            "alphabet_sort_value": sort_value,
            "alphabet_sort_options": [
                {"value": value, "label": label, "selected": value == sort_value}
                for value, label in self.sort_options
            ],
            "alphabet_filters": alphabet_filters,
            "alphabet_selected_price_from": self.request.GET.get("price_from", ""),
            "alphabet_selected_price_to": self.request.GET.get("price_to", ""),
            "alphabet_price_bounds_display": {
                "min": self._format_price_value(price_bounds["min_price"]),
                "max": self._format_price_value(price_bounds["max_price"]),
            },
        }

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            hx_target = self.request.headers.get("HX-Target")
            if hx_target == "alphabet-mobile-listing-root":
                return [self.htmx_mobile_template_name]
            if hx_target == "alphabet-desktop-listing-root":
                return ["pages/alphabet_navigator/desktop/product_listing.html"]
            return [self.htmx_results_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = TemplateView.get_context_data(self, **kwargs)
        context["alphabet_mode"] = "products"
        context.update(self._build_products_mode_context())
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Алфавитный навигатор"},
        ]
        return context


class ProductTab(StrEnum):
    DESCRIPTION = "description"
    REVIEWS = "reviews"
    PAYMENT = "payment"
    HOW_TO_PLAY = "how_to_play"


def _get_active_product_tab(request) -> ProductTab:
    try:
        return ProductTab(request.GET.get("tab"))
    except ValueError:
        return ProductTab.DESCRIPTION


class ProductDetailView(DetailView):
    model = Product
    slug_field = "slug"
    slug_url_kwarg = "slug"
    template_name = "pages/product.html"
    context_object_name = "product"
    tab_templates = {
        ProductTab.DESCRIPTION: "pages/product/desktop/tabs/_description.html",
        ProductTab.REVIEWS: "pages/product/desktop/tabs/_reviews.html",
        ProductTab.PAYMENT: "pages/product/desktop/tabs/_payment.html",
        ProductTab.HOW_TO_PLAY: "pages/product/desktop/tabs/_how_to_play.html",
    }

    queryset = Product.objects.prefetch_related(
        "categories",
        "subtypes",
        "age_groups",
        "files",
        Prefetch("images", queryset=ProductImage.objects.order_by("order")),
        Prefetch(
            "reviews",
            queryset=Review.objects.filter(is_published=True).select_related("user"),
            to_attr="published_reviews_list",
        ),
    )

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["pages/product/desktop/_tabs_panel.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = context["product"]
        active_tab = _get_active_product_tab(self.request)
        images = [image.image.url for image in product.images.all()]
        reviews = getattr(product, "published_reviews_list", [])
        primary_kind = product.subtypes.first() or product.categories.first()
        cart_product_ids = set(get_cart_product_ids(self.request))
        favorite_product_ids = set(get_favorite_product_ids(self.request))
        purchased_product_ids = get_user_product_access_ids(self.request.user, product_ids=[product.id])
        product_is_purchased = product.id in purchased_product_ids
        product_is_favorited = product.id in favorite_product_ids

        context["product_active_tab"] = active_tab
        context["product_active_tab_template"] = self.tab_templates[active_tab]
        context["product_image_urls"] = images or [static("images/example-product-image-1.png")]
        context["product_reviews"] = [
            {
                "author": review.user.review_name,
                "date": review.created_at.strftime("%d.%m.%Y"),
                "rating": review.rating,
                "text": review.comment,
            }
            for review in reviews
        ]
        context["product_kind"] = primary_kind.title if primary_kind else ""
        context["product_price"] = format_price(product.price, product.currency)
        context["product_is_in_cart"] = product.id in cart_product_ids and not product_is_purchased
        context["product_is_purchased"] = product_is_purchased
        context["product_is_favorited"] = product_is_favorited
        context["product_download_url"] = (
            reverse("product-download", kwargs={"product_id": product.id}) if product_is_purchased else ""
        )
        context["product_reviews_count"] = len(reviews)
        context["product_average_rating"] = product.average_rating
        context["product_description_html"] = product.description_as_html()
        context["product_content_html"] = product.content_as_html()

        primary_category = product.categories.first()
        breadcrumbs = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Каталог", "url": reverse("catalog")},
        ]
        if primary_category is not None:
            breadcrumbs.append(
                {
                    "title": primary_category.title,
                    "url": f"{reverse('catalog')}?category={primary_category.slug}",
                },
            )
        breadcrumbs.append({"title": product.title})
        context["breadcrumbs"] = breadcrumbs
        return context


class ProductDownloadView(LoginRequiredMixin, View):
    http_method_names = ["get"]
    login_url = "/?dialog=login"

    def _check_download_rate_limit(self, request, product_id: int):
        user_result = check_rate_limit(
            scope=RateLimitScope.PRODUCT_DOWNLOAD,
            identifier=f"user:{request.user.id}",
            limit=settings.PRODUCT_DOWNLOAD_USER_RATE_LIMIT,
            window_seconds=settings.PRODUCT_DOWNLOAD_USER_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not user_result.allowed:
            return user_result

        return check_rate_limit(
            scope=RateLimitScope.PRODUCT_DOWNLOAD,
            identifier=f"user:{request.user.id}:product:{product_id}",
            limit=settings.PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT,
            window_seconds=settings.PRODUCT_DOWNLOAD_PRODUCT_RATE_LIMIT_WINDOW_SECONDS,
        )

    def _rate_limited_response(self, retry_after_seconds: int):
        response = HttpResponse(PRODUCT_DOWNLOAD_RATE_LIMIT_MESSAGE, status=429)
        response["Retry-After"] = str(retry_after_seconds)
        return response

    def get(self, request, product_id: int, *args, **kwargs):
        if not get_user_product_access_ids(request.user, product_ids=[product_id]):
            log_event(
                logger,
                logging.WARNING,
                "product.download.denied",
                user_id=request.user.id,
                product_id=product_id,
                reason="access_not_found",
            )
            inc_product_download_failed(access_type="user", reason="access_not_found")
            raise Http404("Product download not found")

        rate_limit = self._check_download_rate_limit(request, product_id)
        if not rate_limit.allowed:
            log_event(
                logger,
                logging.WARNING,
                "product.download.rate_limited",
                user_id=request.user.id,
                product_id=product_id,
                retry_after_seconds=rate_limit.retry_after_seconds,
            )
            inc_product_download_failed(access_type="user", reason="rate_limited")
            return self._rate_limited_response(rate_limit.retry_after_seconds)

        product = Product.objects.filter(id=product_id).prefetch_related("files").first()
        if product is None:
            inc_product_download_failed(access_type="user", reason="product_not_found")
            raise Http404("Product download not found")

        product_file = next((item for item in product.files.all() if item.is_active), None)
        if product_file is None:
            log_event(
                logger,
                logging.WARNING,
                "product.download.unavailable",
                user_id=request.user.id,
                product_id=product_id,
                reason="active_file_not_found",
            )
            inc_product_download_failed(access_type="user", reason="active_file_not_found")
            raise Http404("Product download not found")

        try:
            download_url = generate_presigned_download_url(
                file_key=product_file.file_key,
                original_filename=product_file.original_filename or f"{product.slug}.zip",
            )
        except ProductFileDownloadUrlError as exc:
            log_event(
                logger,
                logging.ERROR,
                "product.download.failed",
                exc_info=exc,
                user_id=request.user.id,
                product_id=product_id,
                file_key=product_file.file_key,
                error_type=type(exc).__name__,
            )
            inc_product_download_failed(access_type="user", reason="download_unavailable")
            raise Http404("Product download not found") from exc

        log_event(
            logger,
            logging.INFO,
            "product.download.redirected",
            user_id=request.user.id,
            product_id=product_id,
            file_key=product_file.file_key,
        )
        inc_product_download_redirect(access_type="user")
        return HttpResponseRedirect(download_url)
