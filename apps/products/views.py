from django.templatetags.static import static
from django.views.generic import TemplateView

from .models import Category


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
