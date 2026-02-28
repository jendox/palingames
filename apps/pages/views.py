from enum import StrEnum
from urllib.parse import quote

from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import TemplateView

from .forms import AccountPasswordChangeForm, AccountPersonalDataForm


class AccountTab(StrEnum):
    PERSONAL = "personal"
    ORDERS = "orders"
    FAVORITES = "favorites"
    PASSWORD = "password"


class ProductTab(StrEnum):
    DESCRIPTION = "description"
    REVIEWS = "reviews"
    PAYMENT = "payment"
    HOW_TO_PLAY = "how_to_play"


def _get_active_tab(request) -> AccountTab:
    try:
        return AccountTab(request.GET.get("tab"))
    except ValueError:
        return AccountTab.PERSONAL


def _get_active_product_tab(request) -> ProductTab:
    try:
        return ProductTab(request.GET.get("tab"))
    except ValueError:
        return ProductTab.DESCRIPTION


class HomePageView(TemplateView):
    template_name = "pages/home.html"


class AboutPageView(TemplateView):
    template_name = "pages/about.html"


class PaymentPageView(TemplateView):
    template_name = "pages/payment.html"


class CatalogPageView(TemplateView):
    template_name = "pages/catalog.html"


class ProductPageView(TemplateView):
    template_name = "pages/product.html"
    tab_templates = {
        ProductTab.DESCRIPTION: "pages/product/desktop/tabs/_description.html",
        ProductTab.REVIEWS: "pages/product/desktop/tabs/_reviews.html",
        ProductTab.PAYMENT: "pages/product/desktop/tabs/_payment.html",
        ProductTab.HOW_TO_PLAY: "pages/product/desktop/tabs/_how_to_play.html",
    }
    sample_reviews = [
        {
            "author": "Мария Маляко",
            "date": "14.05.2025",
            "rating": 4,
            "text": (
                "Игра прекрасная. Ребенок очень доволен. "
                "Делали игру всей семьей. Большое спасибо."
            ),
        },
    ]

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ["pages/product/desktop/_tabs_panel.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active_tab = _get_active_product_tab(self.request)
        context["product_active_tab"] = active_tab
        context["product_active_tab_template"] = self.tab_templates[active_tab]
        context["product_reviews"] = self.sample_reviews
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
        context["active_tab_template"] = self.tab_templates[active_tab]
        context["demo_mode"] = self.request.GET.get("demo") == "1"
        context["personal_form"] = personal_form
        context["personal_form_saved"] = personal_form_saved
        context["personal_form_expanded"] = personal_form.is_bound or personal_form_saved
        context["password_form"] = password_form
        context["password_form_saved"] = password_form_saved
        context["password_form_expanded"] = password_form.is_bound or password_form_saved
        return context

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
