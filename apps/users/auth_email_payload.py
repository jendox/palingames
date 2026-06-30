from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site


def serialize_auth_email_context(context: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}

    for key, value in context.items():
        if key == "request":
            continue

        if key == "user" and value is not None:
            safe["user_id"] = value.pk
            continue

        if key == "current_site" and value is not None:
            safe["site_id"] = value.pk
            safe["site_domain"] = value.domain
            continue

        if key == "email" and hasattr(value, "email"):
            safe["email_address"] = value.email
            safe["email_key"] = getattr(value, "key", None)
            continue

        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value

    return safe


def deserialize_auth_email_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = dict(payload["context"].items())

    user_id = payload["context"].get("user_id", None)
    if user_id is not None:
        context["user"] = get_user_model().objects.get(pk=user_id)

    site_id = payload["context"].get("site_id", None)
    if site_id is not None:
        context["current_site"] = Site.objects.get(pk=site_id)

    return context
