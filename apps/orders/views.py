import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.cart.services import clear_cart
from apps.core.logging import log_event
from apps.core.rate_limits import RateLimitScope, check_rate_limit
from apps.payments.jobs import enqueue_invoice_creation
from apps.promocodes.services import PromoCodeError

from .forms import CheckoutSubmitForm
from .models import Order
from .services import (
    clear_checkout_promo_code,
    create_order_from_cart,
    get_checkout_order_context,
    get_checkout_promo_code,
    set_checkout_promo_code,
)

logger = logging.getLogger("apps.checkout")

CHECKOUT_RATE_LIMIT_MESSAGE = "Слишком много попыток оформления заказа. Попробуйте позже."
CHECKOUT_PROMO_RATE_LIMIT_MESSAGE = "Слишком много попыток применения промокода. Попробуйте позже."


def _get_client_ip(request: HttpRequest) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return str(forwarded_for).split(",", maxsplit=1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _check_checkout_create_rate_limit(*, request: HttpRequest, email: str):
    email_result = check_rate_limit(
        scope=RateLimitScope.CHECKOUT_CREATE,
        identifier=f"email:{email}",
        limit=settings.CHECKOUT_CREATE_EMAIL_RATE_LIMIT,
        window_seconds=settings.CHECKOUT_CREATE_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
    )
    if not email_result.allowed:
        return email_result

    ip = _get_client_ip(request)
    if not ip:
        return email_result

    return check_rate_limit(
        scope=RateLimitScope.CHECKOUT_CREATE,
        identifier=f"ip:{ip}",
        limit=settings.CHECKOUT_CREATE_IP_RATE_LIMIT,
        window_seconds=settings.CHECKOUT_CREATE_IP_RATE_LIMIT_WINDOW_SECONDS,
    )


def _check_checkout_promo_apply_rate_limit(*, request: HttpRequest, email: str):
    normalized_email = email.strip().lower()
    if normalized_email:
        email_result = check_rate_limit(
            scope=RateLimitScope.CHECKOUT_PROMO_APPLY,
            identifier=f"email:{normalized_email}",
            limit=settings.CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT,
            window_seconds=settings.CHECKOUT_PROMO_APPLY_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not email_result.allowed:
            return email_result

    ip = _get_client_ip(request)
    if not ip:
        return None

    return check_rate_limit(
        scope=RateLimitScope.CHECKOUT_PROMO_APPLY,
        identifier=f"ip:{ip}",
        limit=settings.CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT,
        window_seconds=settings.CHECKOUT_PROMO_APPLY_IP_RATE_LIMIT_WINDOW_SECONDS,
    )


class CheckoutPageView(TemplateView):
    template_name = "pages/checkout.html"

    def dispatch(self, request, *args, **kwargs):
        checkout_context = get_checkout_order_context(
            request,
            email=request.user.email if request.user.is_authenticated else "",
        )
        if not checkout_context["cart_items"]:
            created_public_id = request.GET.get("created")
            if created_public_id and Order.objects.filter(public_id=created_public_id).exists():
                self.checkout_context = checkout_context
                return super().dispatch(request, *args, **kwargs)

            log_event(logger, logging.INFO, "checkout.redirected_to_cart", reason="empty_cart")
            return redirect("cart")
        self.checkout_context = checkout_context
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get("checkout_form") or CheckoutSubmitForm(
            initial={
                "email": self.request.user.email if self.request.user.is_authenticated else "",
                "promo_code": get_checkout_promo_code(self.request),
            },
        )
        checkout_email = form["email"].value() or ""
        checkout_step = 2 if checkout_email else 1

        context.update(
            get_checkout_order_context(
                self.request,
                email=checkout_email,
                promo_message=kwargs.get("promo_message", ""),
                promo_message_level=kwargs.get("promo_message_level", ""),
            ),
        )
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
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Корзина", "url": reverse("cart")},
            {"title": "Оформление заказа"},
        ]
        context["checkout_error_message"] = kwargs.get("checkout_error_message", "")
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

        rate_limit = _check_checkout_create_rate_limit(
            request=request,
            email=form.cleaned_data["email"],
        )
        if not rate_limit.allowed:
            context = self.get_context_data(
                checkout_form=form,
                checkout_error_message=CHECKOUT_RATE_LIMIT_MESSAGE,
            )
            response = self.render_to_response(context, status=429)
            response["Retry-After"] = str(rate_limit.retry_after_seconds)
            return response

        try:
            order = create_order_from_cart(
                request=request,
                email=form.cleaned_data["email"],
                promo_code=get_checkout_promo_code(request) or form.cleaned_data["promo_code"],
            )
        except PromoCodeError as exc:
            context = self.get_context_data(
                checkout_form=form,
                promo_message=exc.message,
                promo_message_level="error",
            )
            return self.render_to_response(context, status=400)
        except ValueError:
            messages.error(request, "Некоторые товары уже куплены и недоступны для повторного заказа.")
            return redirect("cart")
        enqueue_invoice_creation(order.id)
        clear_cart(request)
        clear_checkout_promo_code(request)
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


def _checkout_summary_template(request) -> str:
    if request.POST.get("checkout_variant") == "mobile":
        return "pages/checkout/_summary_mobile.html"
    return "pages/checkout/_summary_desktop.html"


@require_POST
def checkout_promo_apply_view(request):
    raw_code = request.POST.get("promo_code", "")
    if not raw_code.strip():
        clear_checkout_promo_code(request)
        context = get_checkout_order_context(
            request,
            email=request.POST.get("email", ""),
            promo_message="Введите промокод.",
            promo_message_level="error",
        )
        return render(request, _checkout_summary_template(request), context)

    rate_limit = _check_checkout_promo_apply_rate_limit(
        request=request,
        email=request.POST.get("email", ""),
    )
    if rate_limit is not None and not rate_limit.allowed:
        context = get_checkout_order_context(
            request,
            email=request.POST.get("email", ""),
            promo_message=CHECKOUT_PROMO_RATE_LIMIT_MESSAGE,
            promo_message_level="error",
        )
        response = render(request, _checkout_summary_template(request), context, status=429)
        response["Retry-After"] = str(rate_limit.retry_after_seconds)
        return response

    set_checkout_promo_code(request, raw_code)
    context = get_checkout_order_context(request, email=request.POST.get("email", ""))
    return render(request, _checkout_summary_template(request), context)


@require_POST
def checkout_promo_remove_view(request):
    clear_checkout_promo_code(request)
    context = get_checkout_order_context(
        request,
        email=request.POST.get("email", ""),
        promo_message="Промокод удален.",
        promo_message_level="success",
    )
    return render(request, _checkout_summary_template(request), context)
