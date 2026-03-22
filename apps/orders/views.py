import logging

from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from apps.cart.services import get_cart_page_context
from apps.core.logging import log_event
from apps.payments.jobs import enqueue_invoice_creation

from .forms import CheckoutSubmitForm
from .models import Order
from .services import create_order_from_cart

logger = logging.getLogger("apps.checkout")


class CheckoutPageView(TemplateView):
    template_name = "pages/checkout.html"

    def dispatch(self, request, *args, **kwargs):
        cart_context = get_cart_page_context(request)
        if not cart_context["cart_items"]:
            log_event(logger, logging.INFO, "checkout.redirected_to_cart", reason="empty_cart")
            return redirect("cart")
        self.cart_context = cart_context
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get("checkout_form") or CheckoutSubmitForm(
            initial={"email": self.request.user.email if self.request.user.is_authenticated else ""},
        )
        checkout_email = form["email"].value() or ""
        checkout_step = 2 if checkout_email else 1

        context.update(self.cart_context)
        context["checkout_form"] = form
        context["checkout_email"] = checkout_email
        context["checkout_step"] = checkout_step
        context["checkout_is_authenticated"] = self.request.user.is_authenticated
        created_public_id = self.request.GET.get("created")
        context["checkout_created_order"] = None
        context["checkout_success_redirect_url"] = (
            f"{reverse('account')}?tab=orders" if self.request.user.is_authenticated else reverse("catalog")
        )
        if created_public_id:
            context["checkout_created_order"] = Order.objects.filter(public_id=created_public_id).first()
        return context

    def post(self, request, *args, **kwargs):
        form = CheckoutSubmitForm(request.POST)
        if not form.is_valid():
            log_event(
                logger,
                logging.WARNING,
                "checkout.validation.failed",
                error_fields=sorted(form.errors.keys()),
            )
            context = self.get_context_data(checkout_form=form)
            return self.render_to_response(context, status=400)

        order = create_order_from_cart(request=request, email=form.cleaned_data["email"])
        enqueue_invoice_creation(order.id)
        log_event(
            logger,
            logging.INFO,
            "checkout.order_submitted",
            order_id=order.id,
            order_public_id=order.public_id,
            checkout_type=order.checkout_type,
        )
        checkout_url = f"{reverse('checkout')}?created={order.public_id}"
        return redirect(checkout_url)
