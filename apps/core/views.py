from django.http import HttpResponse, JsonResponse
from django.views import View

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
