from django.views.generic import TemplateView


class HomePageView(TemplateView):
    template_name = "pages/home.html"


class AboutPageView(TemplateView):
    template_name = "pages/about.html"


class PaymentPageView(TemplateView):
    template_name = "pages/payment.html"


class CatalogPageView(TemplateView):
    template_name = "pages/catalog.html"


class AccountPageView(TemplateView):
    template_name = "pages/account.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        active_tab = self.request.GET.get("tab") or "personal"
        if active_tab not in {"personal", "orders", "favorites", "password"}:
            active_tab = "personal"

        user = self.request.user
        display_name = (
            (getattr(user, "first_name", "") or "").strip()
            or (getattr(user, "email", "") or "").strip()
            or (getattr(user, "username", "") or "").strip()
            or "—"
        )

        context["active_tab"] = active_tab
        context["display_name"] = display_name
        context["demo_mode"] = self.request.GET.get("demo") == "1"
        return context

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
