from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest

SESSION_KEY_ANALYTICS_STORAGE = "analytics_storage_consent"
SESSION_KEY_CONSENT_POLICY_VERSION = "cookie_consent_policy_version"
CONSENT_COOKIE_NAME = "palin_consent"


def read_analytics_storage_consent_from_cookie(request: HttpRequest) -> bool | None:
    raw = request.COOKIES.get(CONSENT_COOKIE_NAME)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    version = parsed.get("v")
    analytics = parsed.get("a")
    if not isinstance(version, int) or not isinstance(analytics, bool):
        return None
    if version != settings.COOKIE_CONSENT_POLICY_VERSION:
        return None
    return analytics
