from enum import StrEnum

from django.db.models import Count, Max, Min, Prefetch
from django.core.paginator import Paginator
from django.templatetags.static import static
from django.views.generic import DetailView, TemplateView

from .models import AgeGroupTag, Category, DevelopmentAreaTag, Product, ProductImage, Review, SubType, Theme


class CatalogView(TemplateView):
    template_name = "pages/catalog.html"
    htmx_results_template_name = "pages/catalog/desktop/results_panel.html"
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

    def _build_product_card(self, product):
        primary_kind = product.subtypes.first() or product.categories.first()
        primary_image = next(iter(product.images.all()), None)
        return {
            "title": product.title,
            "url": product.get_absolute_url(),
            "price": f"{product.price:.2f}".replace(".", ",") + " BYN",
            "kind": primary_kind.title if primary_kind else "",
            "image_url": primary_image.image.url if primary_image else static("images/example-product-image-1.png"),
        }

    def _format_price_value(self, value):
        if value is None:
            return ""
        return f"{value:.2f}".replace(".", ",")

    def _selected_values(self, key):
        return self.request.GET.getlist(key)

    def _apply_filters(self, queryset):
        subtype_ids = self._selected_values("subtype")
        age_ids = self._selected_values("age")
        area_ids = self._selected_values("area")
        theme_ids = self._selected_values("theme")
        price_from = self.request.GET.get("price_from")
        price_to = self.request.GET.get("price_to")

        if subtype_ids:
            queryset = queryset.filter(subtypes__id__in=subtype_ids)
        if age_ids:
            queryset = queryset.filter(age_groups__id__in=age_ids)
        if area_ids:
            queryset = queryset.filter(development_areas__id__in=area_ids)
        if theme_ids:
            queryset = queryset.filter(themes__id__in=theme_ids)
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

    def get_template_names(self):
        category_slug = self.request.GET.get("category")
        if self.request.headers.get("HX-Request") == "true" and category_slug:
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

        subtype_options = self._filter_option_queryset(SubType, "products", category_queryset, category=selected_category)
        age_options = self._filter_option_queryset(AgeGroupTag, "products", category_queryset)
        area_options = self._filter_option_queryset(DevelopmentAreaTag, "products", category_queryset)
        theme_options = self._filter_option_queryset(Theme, "products", category_queryset)

        paginator = Paginator(sorted_queryset, 9)
        page_obj = paginator.get_page(page_number)

        context["catalog_products"] = [self._build_product_card(product) for product in page_obj.object_list]
        context["catalog_products_count"] = paginator.count
        context["catalog_page_obj"] = page_obj
        context["catalog_pagination"] = {
            "current": page_obj.number,
            "total": paginator.num_pages,
            "has_previous": page_obj.has_previous(),
            "has_next": page_obj.has_next(),
            "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
            "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
            "pages": list(paginator.page_range),
        }
        context["catalog_sort_value"] = sort_value
        context["catalog_sort_options"] = [
            {"value": value, "label": label, "selected": value == sort_value}
            for value, label in self.sort_options
        ]
        context["catalog_selected_filters"] = {
            "subtypes": selected_subtypes,
            "ages": selected_ages,
            "areas": selected_areas,
            "themes": selected_themes,
        }
        context["catalog_price_bounds"] = {
            "min": price_bounds["min_price"],
            "max": price_bounds["max_price"],
        }
        context["catalog_selected_price_from"] = self.request.GET.get("price_from", "")
        context["catalog_selected_price_to"] = self.request.GET.get("price_to", "")
        context["catalog_price_bounds_display"] = {
            "min": self._format_price_value(price_bounds["min_price"]),
            "max": self._format_price_value(price_bounds["max_price"]),
        }
        context["catalog_filters"] = {
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
        context["catalog_quick_filters"] = [
            {
                "id": option["id"],
                "label": option["label"],
                "selected": option["selected"],
            }
            for option in context["catalog_filters"]["areas"][:5]
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
