from __future__ import annotations

from django.conf import settings
from django.core.cache import cache

from apps.core.alerts import send_incident_alert

PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY = "payments.webhook.failures"

PAYMENT_WEBHOOK_ALERTABLE_REASONS = {
    "invoice_not_found",
    "processing_error",
}


def _build_payment_webhook_failure_fingerprint(*, provider: str, reason: str) -> str:
    return f"{PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY}:{provider}:{reason}"


def _get_payment_webhook_failure_counter_key(*, provider: str, reason: str) -> str:
    return f"payment-webhook-failure:{provider}:{reason}"


def record_payment_webhook_failure_incident(*, provider: str, reason: str) -> bool:
    if reason not in PAYMENT_WEBHOOK_ALERTABLE_REASONS:
        return False

    threshold = settings.PAYMENT_WEBHOOK_INCIDENT_THRESHOLD
    window_seconds = settings.PAYMENT_WEBHOOK_INCIDENT_WINDOW_SECONDS
    cache_key = _get_payment_webhook_failure_counter_key(provider=provider, reason=reason)

    if cache.add(cache_key, 1, timeout=window_seconds):
        failures = 1
    else:
        failures = cache.incr(cache_key)

    if failures != threshold:
        return False

    return send_incident_alert(
        key=PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY,
        title="Repeated payment webhook failures",
        severity="critical",
        fingerprint=_build_payment_webhook_failure_fingerprint(
            provider=provider,
            reason=reason,
        ),
        details={
            "provider": provider,
            "reason": reason,
            "failures": failures,
            "window_minutes": window_seconds // 60,
        },
    )
