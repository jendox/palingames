
from django.http import JsonResponse
from django.views import View

from .health import build_readiness_report


class HealthLiveView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        return JsonResponse({"status": "ok"})


class HealthReadyView(View):
    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        checks = build_readiness_report()
        is_ready = all(check["status"] == "ok" for check in checks.values())
        return JsonResponse(
            {
                "status": "ok" if is_ready else "degraded",
                "checks": checks,
            },
            status=200 if is_ready else 503,
        )
