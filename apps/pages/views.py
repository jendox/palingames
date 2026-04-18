from enum import StrEnum
from urllib.parse import quote

from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from apps.access.services import get_user_product_access_ids
from apps.favorites.services import get_account_favorites_context
from apps.orders.models import Order
from apps.products.pricing import format_price

from .forms import AccountPasswordChangeForm, AccountPersonalDataForm


class AccountTab(StrEnum):
    PERSONAL = "personal"
    ORDERS = "orders"
    FAVORITES = "favorites"
    PASSWORD = "password"


def _get_active_tab(request) -> AccountTab:
    try:
        return AccountTab(request.GET.get("tab"))
    except ValueError:
        return AccountTab.PERSONAL


class HomePageView(TemplateView):
    template_name = "pages/home.html"


class AboutPageView(TemplateView):
    template_name = "pages/about.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "О нас"},
        ]
        return context


class PaymentPageView(TemplateView):
    template_name = "pages/payment.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Оплата"},
        ]
        return context


class CustomGamePageView(TemplateView):
    template_name = "pages/custom_game.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Игра на заказ"},
        ]
        return context


class AccountPageView(TemplateView):
    template_name = "pages/account.html"
    tab_templates = {
        AccountTab.PERSONAL: "pages/account/desktop/tabs/personal.html",
        AccountTab.ORDERS: "pages/account/desktop/tabs/orders.html",
        AccountTab.FAVORITES: "pages/account/desktop/tabs/favorites.html",
        AccountTab.PASSWORD: "pages/account/desktop/tabs/password.html",
    }

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            next_path = quote(request.get_full_path() or "/account/")
            return redirect(f"/?dialog=login&next={next_path}")
        return super().dispatch(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            hx_target = self.request.headers.get("HX-Target", "").strip().lstrip("#")
            is_favorites_tab = _get_active_tab(self.request) == AccountTab.FAVORITES
            if hx_target == "account-favorites-desktop-results" and is_favorites_tab:
                return ["pages/account/desktop/_account_favorites_desktop_results.html"]
            return ["pages/account/desktop/_shell.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        active_tab = _get_active_tab(self.request)

        user = self.request.user
        personal_form = kwargs.get("personal_form") or AccountPersonalDataForm(instance=user)
        personal_form_saved = kwargs.get("personal_form_saved", False)
        password_form = kwargs.get("password_form") or AccountPasswordChangeForm(user=user)
        password_form_saved = kwargs.get("password_form_saved", False)

        context["active_tab"] = active_tab
        context["open_account_favorites_mobile"] = active_tab == AccountTab.FAVORITES
        context["active_tab_template"] = self.tab_templates[active_tab]
        context["demo_mode"] = self.request.GET.get("demo") == "1"
        context["personal_form"] = personal_form
        context["personal_form_saved"] = personal_form_saved
        context["personal_form_expanded"] = personal_form.is_bound or personal_form_saved
        context["password_form"] = password_form
        context["password_form_saved"] = password_form_saved
        context["password_form_expanded"] = password_form.is_bound or password_form_saved
        context["account_orders"] = self._get_orders_context()
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Личный кабинет"},
        ]
        context.update(get_account_favorites_context(self.request))
        return context

    def _get_orders_context(self) -> list[dict]:
        orders = list(
            Order.objects.filter(user=self.request.user)
            .select_related("invoice")
            .prefetch_related("items")
            .order_by("-created_at", "-id"),
        )
        product_ids = [item.product_id for order in orders for item in order.items.all()]

        status_meta = {
            Order.OrderStatus.CREATED: {
                "label": "Создан",
                "classes": "bg-[rgba(242,171,39,0.14)] text-[var(--color-orange)]",
                "action_label": None,
                "action_url": None,
            },
            Order.OrderStatus.PENDING: {
                "label": "В обработке",
                "classes": "bg-[rgba(242,171,39,0.14)] text-[var(--color-orange)]",
                "action_label": None,
                "action_url": None,
            },
            Order.OrderStatus.WAITING_FOR_PAYMENT: {
                "label": "Ожидает оплату",
                "classes": "bg-[rgba(101,197,194,0.16)] text-[var(--color-turquoise)]",
                "action_label": "Оплатить",
                "action_url": None,
            },
            Order.OrderStatus.PAID: {
                "label": "Оплачен",
                "classes": "bg-[rgba(128,193,30,0.16)] text-[var(--color-green)]",
                "action_label": "Оплачен",
                "action_url": None,
            },
            Order.OrderStatus.CANCELED: {
                "label": "Отменён",
                "classes": "bg-[rgba(212,90,90,0.12)] text-[#D45A5A]",
                "action_label": None,
                "action_url": None,
            },
            Order.OrderStatus.FAILED: {
                "label": "Ошибка",
                "classes": "bg-[rgba(212,90,90,0.12)] text-[#D45A5A]",
                "action_label": None,
                "action_url": None,
            },
        }
        purchased_product_ids = get_user_product_access_ids(self.request.user, product_ids=product_ids)

        result = []
        for order in orders:
            invoice = getattr(order, "invoice", None)
            meta = status_meta[order.status].copy()
            if invoice and order.status == Order.OrderStatus.WAITING_FOR_PAYMENT:
                meta["action_url"] = invoice.invoice_url

            items = [
                {
                    "title": item.title_snapshot,
                    "category": item.category_snapshot,
                    "price": format_price(item.line_total_amount, order.currency),
                    "has_discount": item.promo_eligible and item.discounted_line_total_amount is not None,
                    "discounted_price": (
                        format_price(item.discounted_line_total_amount, order.currency)
                        if item.discounted_line_total_amount is not None
                        else ""
                    ),
                    "image_url": item.product_image_snapshot,
                    "download_url": (
                        reverse("product-download", kwargs={"product_id": item.product_id})
                        if item.product_id in purchased_product_ids
                        else ""
                    ),
                }
                for item in order.items.all()
            ]

            result.append(
                {
                    "number": order.payment_account_no,
                    "date": order.created_at.strftime("%d.%m.%Y"),
                    "total": format_price(order.total_amount, order.currency),
                    "has_discount": order.discount_amount > 0 and bool(order.promo_code_snapshot),
                    "promo_code": order.promo_code_snapshot,
                    "discount": format_price(order.discount_amount, order.currency),
                    "items_count": order.items_count,
                    "status": meta["label"],
                    "status_classes": meta["classes"],
                    "action_label": meta["action_label"],
                    "action_url": meta["action_url"],
                    "is_paid": order.status == Order.OrderStatus.PAID,
                    "invoice_status": invoice.status if invoice else None,
                    "items": items,
                },
            )

        return result

    def _personal_data_update(self, request) -> HttpResponse:
        form = AccountPersonalDataForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            context = self.get_context_data(personal_form=form, personal_form_saved=True)
            return self.render_to_response(context)

        context = self.get_context_data(personal_form=form)
        return self.render_to_response(context)

    def _password_update(self, request) -> HttpResponse:
        form = AccountPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            context = self.get_context_data(
                password_form=AccountPasswordChangeForm(user=user),
                password_form_saved=True,
            )
            return self.render_to_response(context)

        context = self.get_context_data(password_form=form)
        return self.render_to_response(context)

    def _default_tab(self, request, *args, **kwargs) -> HttpResponse:
        return self.get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs) -> HttpResponse:

        active_tab = _get_active_tab(request)
        tab_post_handlers = {
            AccountTab.PERSONAL: self._personal_data_update,
            AccountTab.PASSWORD: self._password_update,
            AccountTab.FAVORITES: self._default_tab,
            AccountTab.ORDERS: self._default_tab,
        }
        return tab_post_handlers[active_tab](request, *args, **kwargs)
