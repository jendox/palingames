from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.orders.models import Order
from apps.users.models import PersonalDataProcessingConsentLog

User = get_user_model()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def record_personal_data_consent(
    *,
    email: str,
    source: str,
    user: User | None = None,
    order: Order | None = None,
    granted: bool = True,
    policy_version: int | None = None,
) -> PersonalDataProcessingConsentLog:
    version = policy_version if policy_version is not None else settings.PERSONAL_DATA_POLICY_VERSION
    return PersonalDataProcessingConsentLog.objects.create(
        user=user if user and getattr(user, "is_authenticated", False) else None,
        email=_normalize_email(email),
        order=order,
        policy_version=version,
        granted=granted,
        source=source,
    )
