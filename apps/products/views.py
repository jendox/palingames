from enum import StrEnum

from django.db.models import Prefetch
from django.templatetags.static import static
from django.views.generic import DetailView, TemplateView

from .models import Category, Product, ProductImage, Review


class CatalogView(TemplateView):
    template_name = "pages/catalog.html"
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
