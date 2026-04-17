from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.products.models import Product

from .services import get_favorite_product_ids, get_favorites_page_context, toggle_favorite_product


class FavoritesPageView(TemplateView):
    template_name = "pages/favorites.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(f"{reverse('account')}?tab=favorites")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_favorites_page_context(self.request))
        return context


@require_POST
def favorite_toggle_view(request):
    product_id_raw = request.POST.get("product_id")
    try:
        product_id = int(product_id_raw)
    except (TypeError, ValueError):
        raise Http404("Product not found")

    if not Product.objects.filter(id=product_id).exists():
        raise Http404("Product not found")

    result = toggle_favorite_product(request, product_id)
    favorite_ids = get_favorite_product_ids(request)
    return JsonResponse(
        {
            "ok": True,
            "is_favorited": result["is_favorited"],
            "favorites_count": len(favorite_ids),
        },
    )
