from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from .consent import (
    SESSION_KEY_ANALYTICS_STORAGE,
    SESSION_KEY_CONSENT_POLICY_VERSION,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CookieConsentApiView(View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        return JsonResponse(
            {
                "policy_version": settings.COOKIE_CONSENT_POLICY_VERSION,
                "analytics_storage_consent": request.session.get(SESSION_KEY_ANALYTICS_STORAGE),
            },
        )

    def post(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        try:
            data = json.loads(request.body.decode())
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_json"}, status=400)

        if data.get("policy_version") != settings.COOKIE_CONSENT_POLICY_VERSION:
            return JsonResponse({"error": "policy_version_mismatch"}, status=400)

        raw = data.get("analytics_storage")
        if not isinstance(raw, bool):
            return JsonResponse({"error": "invalid_analytics_storage"}, status=400)

        request.session[SESSION_KEY_ANALYTICS_STORAGE] = raw
        request.session[SESSION_KEY_CONSENT_POLICY_VERSION] = int(data["policy_version"])

        return JsonResponse({"ok": True})
