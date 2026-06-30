from __future__ import annotations

import logging
from typing import Any

from allauth.account.adapter import get_adapter
from allauth.core.context import request_context
from django.contrib.sites.models import Site
from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import RequestFactory

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event
from apps.users.auth_email_payload import deserialize_auth_email_context

logger = logging.getLogger("apps.users.auth_email")

_REQUEST_FACTORY = RequestFactory()


def _build_render_context(*, recipient: str, payload: dict[str, Any]) -> dict[str, Any]:
    context = deserialize_auth_email_context(payload)
    context.setdefault(
        "logo_url",
        build_absolute_url(staticfiles_storage.url("images/logo.svg")),
    )

    site = context.get("current_site")
    if site is None:
        site = Site.objects.get_current()

    ctx: dict[str, Any] = {
        "email": recipient,
        "current_site": site,
    }
    ctx.update(context)

    return ctx


def send_auth_email(*, template_prefix: str, recipient: str, payload: dict[str, Any]) -> None:
    adapter = get_adapter()

    context = _build_render_context(recipient=recipient, payload=payload)

    request = _REQUEST_FACTORY.get("/")

    with request_context(request):
        context["request"] = request
        msg = adapter.render_mail(template_prefix, recipient, context)
        msg.send()

    log_event(
        logger,
        logging.INFO,
        "auth.email.sent",
        template_prefix=template_prefix,
        recipient=recipient,
    )
