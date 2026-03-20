from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.products.models import Product

from .services import (
    clear_cart,
    get_cart_page_context,
    get_cart_product_ids,
    remove_cart_product,
    toggle_cart_product,
)


class CartPageView(TemplateView):
    template_name = "pages/cart.html"
    htmx_template_name = "pages/cart/_content.html"

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return [self.htmx_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(get_cart_page_context(self.request))
        return context


@require_POST
def cart_toggle_view(request):
    product_id_raw = request.POST.get("product_id")
    try:
        product_id = int(product_id_raw)
    except (TypeError, ValueError):
        raise Http404("Product not found")

    if not Product.objects.filter(id=product_id).exists():
        raise Http404("Product not found")

    in_cart = toggle_cart_product(request, product_id)
    cart_ids = get_cart_product_ids(request)
    return JsonResponse(
        {
            "ok": True,
            "in_cart": in_cart,
            "cart_count": len(cart_ids),
        },
    )


@require_POST
def cart_remove_view(request, product_id: int):
    remove_cart_product(request, product_id)
    if request.headers.get("HX-Request") == "true":
        return render(request, "pages/cart/_content.html", get_cart_page_context(request))
    messages.success(request, "Товар удален из корзины.")
    return redirect("cart")


@require_POST
def cart_clear_view(request):
    clear_cart(request)
    messages.success(request, "Корзина очищена.")
    return redirect("cart")
