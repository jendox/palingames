from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View

from apps.core.seo import build_absolute_url
from apps.products.models import Product

from .health import build_readiness_report
from .metrics import inc_health_readiness_check, metrics_response


class HealthLiveView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        return JsonResponse({"status": "ok"})


class HealthReadyView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        checks = build_readiness_report()
        for component, result in checks.items():
            inc_health_readiness_check(component=component, status=result["status"])
        is_ready = all(check["status"] == "ok" for check in checks.values())
        return JsonResponse(
            {
                "status": "ok" if is_ready else "degraded",
                "checks": checks,
            },
            status=200 if is_ready else 503,
        )


class MetricsView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        payload, content_type = metrics_response()
        return HttpResponse(payload, content_type=content_type)


class RobotsTxtView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        payload = render_to_string(
            "seo/robots.txt",
            {
                "sitemap_url": build_absolute_url(reverse("sitemap-xml")),
            },
        )
        return HttpResponse(payload, content_type="text/plain; charset=utf-8")


class SitemapXmlView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        static_urls = [
            {"location": build_absolute_url(reverse("home"))},
            {"location": build_absolute_url(reverse("catalog"))},
            {"location": build_absolute_url(reverse("about"))},
            {"location": build_absolute_url(reverse("payment"))},
            {"location": build_absolute_url(reverse("custom-game"))},
        ]
        product_urls = [
            {
                "location": build_absolute_url(product.get_absolute_url()),
                "lastmod": product.updated_at.date().isoformat(),
            }
            for product in Product.objects.order_by("id")
        ]
        payload = render_to_string(
            "seo/sitemap.xml",
            {
                "urls": [*static_urls, *product_urls],
            },
        )
        return HttpResponse(payload, content_type="application/xml; charset=utf-8")
