from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404
from django.templatetags.static import static
from django.urls import reverse
from django.views.generic import TemplateView

from apps.access.services import get_user_product_access_ids
from apps.cart.services import get_cart_product_ids
from apps.core.metrics import inc_collection_page_view
from apps.core.seo import build_breadcrumbs_json_ld, build_seo_context, normalize_seo_description
from apps.favorites.services import get_favorite_product_ids

from .mixins import CatalogListingMixin
from .models import ProductCollection
from .services.collections import (
    get_collection_products_queryset,
    get_public_collection_or_404,
    get_public_collections_queryset,
)


def _metrics_user_type(user) -> str:
    return "authenticated" if getattr(user, "is_authenticated", False) else "guest"


class CollectionFeatureRequireMixin:
    def dispatch(self, request, *args, **kwargs):
        if not settings.COLLECTIONS_ENABLED:
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class CollectionListingMixin(CatalogListingMixin):
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

    def _build_collection_products_context(self, products_queryset):
        sort_value = self.request.GET.get("sort", "").strip()
        page_number = self.request.GET.get("page") or 1

        if sort_value:
            products_queryset = self._apply_sort(products_queryset, sort_value)

        cart_product_ids = set(get_cart_product_ids(self.request))
        favorite_product_ids = set(get_favorite_product_ids(self.request))
        product_ids = list(products_queryset.values_list("id", flat=True))
        purchased_product_ids = get_user_product_access_ids(self.request.user, product_ids=product_ids)

        desktop_paginator = Paginator(products_queryset, 9)
        desktop_page_obj = desktop_paginator.get_page(page_number)

        mobile_paginator = Paginator(products_queryset, 8)
        mobile_page_obj = mobile_paginator.get_page(page_number)

        catalog_products = [
            self._build_product_card(
                product,
                cart_product_ids=cart_product_ids,
                purchased_product_ids=purchased_product_ids,
                favorite_product_ids=favorite_product_ids,
            )
            for product in desktop_page_obj.object_list
        ]
        catalog_mobile_products = [
            self._build_product_card(
                product,
                cart_product_ids=cart_product_ids,
                purchased_product_ids=purchased_product_ids,
                favorite_product_ids=favorite_product_ids,
            )
            for product in mobile_page_obj.object_list
        ]

        return {
            "catalog_products": catalog_products,
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
            "catalog_mobile_products": catalog_mobile_products,
            "catalog_mobile_products_count": mobile_paginator.count,
            "catalog_mobile_page_obj": mobile_page_obj,
            "catalog_mobile_pagination_items": self._build_mobile_pagination(mobile_page_obj),
            "catalog_sort_value": sort_value,
            "catalog_sort_options": [
                {"value": value, "label": label, "selected": value == sort_value}
                for value, label in self.sort_options
            ],
        }

    def _build_collection_card(self, collection: ProductCollection, *, style_index: int = 0) -> dict:
        cover_url = static("images/logo.svg")
        if collection.cover_image:
            cover_url = collection.cover_image.url
        style = self.card_styles[style_index % len(self.card_styles)]
        return {
            "title": collection.title,
            "slug": collection.slug,
            "url": collection.get_absolute_url(),
            "short_description": collection.short_description,
            "badge": collection.badge,
            "cover_url": cover_url,
            **style,
        }


class CollectionIndexView(CollectionFeatureRequireMixin, CollectionListingMixin, TemplateView):
    template_name = "pages/collections/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        inc_collection_page_view(page_type="collections_index", user_type=_metrics_user_type(self.request.user))

        breadcrumbs = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Тематические подборки"},
        ]
        context["breadcrumbs"] = breadcrumbs
        context["collection_cards"] = [
            self._build_collection_card(collection, style_index=index)
            for index, collection in enumerate(get_public_collections_queryset())
        ]
        context.update(
            build_seo_context(
                title="Тематические подборки — PalinGames",
                description=(
                    "Тематические подборки развивающих материалов и игр PalinGames. "
                    "PDF к печати для детей, родителей и педагогов."
                ),
                canonical_url=reverse("collections"),
                robots="index,follow",
                json_ld=build_breadcrumbs_json_ld(breadcrumbs),
            ),
        )
        return context


class CollectionDetailView(CollectionFeatureRequireMixin, CollectionListingMixin, TemplateView):
    template_name = "pages/collection.html"
    htmx_desktop_results_template_name = "pages/collection/desktop/results_panel.html"

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            hx_target = self.request.headers.get("HX-Target", "").strip().lstrip("#")
            if hx_target == "collection-desktop-results":
                return [self.htmx_desktop_results_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collection = get_public_collection_or_404(kwargs["slug"])
        inc_collection_page_view(page_type="collection", user_type=_metrics_user_type(self.request.user))

        breadcrumbs = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Тематические подборки", "url": reverse("collections")},
            {"title": collection.title},
        ]
        context["collection"] = collection
        context["breadcrumbs"] = breadcrumbs
        context.update(
            self._build_collection_products_context(get_collection_products_queryset(collection)),
        )
        context["collection_analytics_context"] = {
            "collection_slug": collection.slug,
            "collection_title": collection.title,
        }

        has_extra_params = any(self.request.GET.get(param) for param in ("sort", "page"))
        seo_title = collection.seo_title or f"{collection.title} — PalinGames"
        seo_description = normalize_seo_description(
            collection.seo_description or collection.short_description or collection.description,
        )
        context.update(
            build_seo_context(
                title=seo_title,
                description=seo_description,
                canonical_url=collection.get_absolute_url(),
                robots=collection.robots if not has_extra_params else "noindex,follow",
                image_url=collection.cover_image.url if collection.cover_image else None,
                json_ld=build_breadcrumbs_json_ld(breadcrumbs),
            ),
        )
        return context
