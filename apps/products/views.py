from enum import StrEnum

from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Prefetch
from django.templatetags.static import static
from django.views.generic import DetailView, TemplateView

from apps.cart.services import get_cart_product_ids

from .models import AgeGroupTag, Category, DevelopmentAreaTag, Product, ProductImage, Review, SubType, Theme


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

    def _build_product_card(self, product, selected_category=None, cart_product_ids=None):
        primary_kind = product.subtypes.first() or product.categories.first()
        primary_image = next(iter(product.images.all()), None)
        cart_ids = cart_product_ids or set()
        return {
            "id": product.id,
            "title": product.title,
            "url": product.get_absolute_url(),
            "price": f"{product.price:.2f}".replace(".", ",") + " BYN",
            "kind": primary_kind.title if primary_kind else "",
            "category": self._format_category_label(selected_category or product.categories.first()),
            "rating": f"{product.average_rating:.1f}".replace(".", ","),
            "is_favorited": False,
            "is_in_cart": product.id in cart_ids,
            "image_url": primary_image.image.url if primary_image else static("images/example-product-image-1.png"),
        }

    def _format_price_value(self, value):
        if value is None:
            return ""
        return f"{value:.2f}".replace(".", ",")

    def _selected_values(self, key):
        return self.request.GET.getlist(key)

    def _build_mobile_pagination(self, page_obj):
        total_pages = page_obj.paginator.num_pages
        current_page = page_obj.number

        if total_pages <= 1:
            return []

        items = []

        def add_page(page_number):
            items.append(
                {
                    "type": "page",
                    "number": page_number,
                    "current": page_number == current_page,
                }
            )

        def add_ellipsis():
            if items and items[-1]["type"] == "ellipsis":
                return
            items.append({"type": "ellipsis"})

        if total_pages <= 3:
            for page_number in range(1, total_pages + 1):
                add_page(page_number)
            return items

        if current_page <= 2:
            add_page(1)
            add_page(2)
            add_page(3)
            add_ellipsis()
            add_page(total_pages)
            return items

        if current_page >= total_pages - 1:
            add_page(1)
            add_ellipsis()
            add_page(total_pages - 2)
            add_page(total_pages - 1)
            add_page(total_pages)
            return items

        add_page(1)
        add_ellipsis()
        add_page(current_page)
        add_ellipsis()
        add_page(total_pages)
        return items

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
        subtype_options = self._filter_option_queryset(
            SubType,
            "products",
            category_queryset,
            category=selected_category,
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

    def _build_products_mode_context(self, selected_category):
        sort_value = self.request.GET.get("sort", "")
        page_number = self.request.GET.get("page") or 1
        category_queryset = self._base_products_queryset().filter(categories=selected_category)
        price_bounds = category_queryset.aggregate(min_price=Min("price"), max_price=Max("price"))
        filtered_queryset = self._apply_filters(category_queryset)
        sorted_queryset = self._apply_sort(filtered_queryset, sort_value)

        selected_subtypes = self._selected_values("subtype")
        selected_ages = self._selected_values("age")
        selected_areas = self._selected_values("area")
        selected_themes = self._selected_values("theme")

        catalog_filters, catalog_quick_filters = self._build_catalog_filters_context(
            selected_category,
            category_queryset,
            selected_subtypes,
            selected_ages,
            selected_areas,
            selected_themes,
        )

        cart_product_ids = set(get_cart_product_ids(self.request))

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
        }

    def get_template_names(self):
        category_slug = self.request.GET.get("category")
        if self.request.headers.get("HX-Request") == "true" and category_slug:
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

        context["selected_category"] = selected_category
        context["catalog_mode"] = "products" if selected_category else "categories"

        if not selected_category:
            return context

        context.update(self._build_products_mode_context(selected_category))
        return context


class AlphabetNavigatorView(CatalogView):
    template_name = "pages/alphabet_navigator.html"
    htmx_results_template_name = "pages/alphabet_navigator/desktop/product_listing.html"
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

    def _build_alphabet_filters_context(self, queryset, selected_subtypes, selected_ages, selected_areas, selected_themes):
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

    def _build_products_mode_context(self, selected_category=None):
        sort_value = self.request.GET.get("sort", "")
        page_number = self.request.GET.get("page") or 1
        selected_letter = self._selected_letter()

        base_queryset = self._base_products_queryset().filter(title__istartswith=selected_letter)
        price_bounds = base_queryset.aggregate(min_price=Min("price"), max_price=Max("price"))
        filtered_queryset = self._apply_filters(base_queryset)
        sorted_queryset = self._apply_sort(filtered_queryset, sort_value)
        cart_product_ids = set(get_cart_product_ids(self.request))

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
                self._build_product_card(product, cart_product_ids=cart_product_ids)
                for product in desktop_page_obj.object_list
            ],
            "alphabet_products_count": desktop_paginator.count,
            "alphabet_page_obj": desktop_page_obj,
            "alphabet_mobile_products": [
                self._build_product_card(product, cart_product_ids=cart_product_ids)
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
            if self.request.headers.get("HX-Target") == "alphabet-mobile-listing-root":
                return [self.htmx_mobile_template_name]
            return [self.htmx_results_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = TemplateView.get_context_data(self, **kwargs)
        context["alphabet_mode"] = "products"
        context.update(self._build_products_mode_context())
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
        context["product_price"] = f"{product.price:.2f}".replace(".", ",") + " BYN"
        context["product_reviews_count"] = len(reviews)
        context["product_average_rating"] = product.average_rating
        context["product_description_html"] = product.description_as_html()
        context["product_content_html"] = product.content_as_html()
        return context
