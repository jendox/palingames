from enum import StrEnum
from urllib.parse import quote

from django.contrib.auth import update_session_auth_hash
from django.http import HttpResponse
from django.shortcuts import redirect
from django.templatetags.static import static
from django.views.generic import TemplateView

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


class PaymentPageView(TemplateView):
    template_name = "pages/payment.html"


class CartPageView(TemplateView):
    template_name = "pages/cart.html"
    sample_cart_items = [
        {
            "title": "Мой первый английский",
            "kind": "Интерактивный плакат",
            "price": "1,2 BYN",
            "image_url": static("images/example-product-image-1.png"),
        },
        {
            "title": "Белорусский национальный строй",
            "kind": "Дидактическая игра",
            "price": "1,5 BYN",
            "image_url": static("images/example-product-image-2.png"),
        },
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cart_items"] = self.sample_cart_items
        context["cart_total"] = "2,7 BYN"
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
