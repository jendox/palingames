from django.shortcuts import redirect
from django.views.generic import TemplateView

from apps.cart.services import get_cart_page_context


class CheckoutPageView(TemplateView):
    template_name = "pages/checkout.html"

    def dispatch(self, request, *args, **kwargs):
        cart_context = get_cart_page_context(request)
        if not cart_context["cart_items"]:
            return redirect("cart")
        self.cart_context = cart_context
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checkout_email = self.request.user.email if self.request.user.is_authenticated else ""
        checkout_step = 2 if checkout_email else 1

        context.update(self.cart_context)
        context["checkout_email"] = checkout_email
        context["checkout_step"] = checkout_step
        context["checkout_is_authenticated"] = self.request.user.is_authenticated
        return context
