from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from apps.core.rate_limits import get_client_ip
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
    client_ip = get_client_ip(request) or None
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
