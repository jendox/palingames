from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from apps.orders.models import Order
from apps.users.models import PersonalDataProcessingConsentLog

User = get_user_model()


@dataclass(frozen=True)
class PersonalDataContext:
    email: str
    source: str
    granted: bool = True
    user: User | None = None
    order: Order | None = None
    policy_version: int | None = None
    ip: str | None = None
    user_agent: str = ""


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_client_ip_and_ua(request: HttpRequest) -> tuple[str | None, str]:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    client_ip: str | None = None
    if x_forwarded_for:
        client_ip = str(x_forwarded_for).split(",", maxsplit=1)[0].strip() or None
    if not client_ip:
        client_ip = request.META.get("REMOTE_ADDR") or None
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:256]

    return client_ip, ua


def record_personal_data_consent(ctx: PersonalDataContext) -> PersonalDataProcessingConsentLog:
    version = ctx.policy_version if ctx.policy_version is not None else settings.PERSONAL_DATA_POLICY_VERSION
    ua = (ctx.user_agent or "")[:256]

    return PersonalDataProcessingConsentLog.objects.create(
        user=ctx.user if ctx.user and getattr(ctx.user, "is_authenticated", False) else None,
        email=_normalize_email(ctx.email),
        order=ctx.order,
        policy_version=version,
        granted=ctx.granted,
        source=ctx.source,
        ip=ctx.ip,
        user_agent=ua,
    )
