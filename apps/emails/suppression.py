from __future__ import annotations

from .models import EmailSuppression


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_active_suppression(email: str) -> EmailSuppression | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    return EmailSuppression.objects.filter(email=normalized, active=True).first()


def is_email_suppressed(email: str) -> bool:
    return get_active_suppression(email) is not None
