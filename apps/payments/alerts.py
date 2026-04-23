from __future__ import annotations

from django.conf import settings

from apps.core.alerts import ThresholdIncidentSpec, record_threshold_incident, resolve_threshold_incident

PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY = "payments.webhook.failures"
PAYMENT_STATUS_SYNC_FAILURE_INCIDENT_KEY = "payments.status_sync.failures"

PAYMENT_WEBHOOK_ALERTABLE_REASONS = {
    "invoice_not_found",
    "processing_error",
}


def _build_payment_webhook_failure_fingerprint(*, provider: str, reason: str) -> str:
    return f"{PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY}:{provider}:{reason}"


def _get_payment_webhook_failure_counter_key(*, provider: str, reason: str) -> str:
    return f"payment-webhook-failure:{provider}:{reason}"


def _build_payment_status_sync_failure_fingerprint(*, provider: str) -> str:
    return f"{PAYMENT_STATUS_SYNC_FAILURE_INCIDENT_KEY}:{provider}"


def _get_payment_status_sync_failure_counter_key(*, provider: str) -> str:
    return f"payment-status-sync-failure:{provider}"


def record_payment_webhook_failure_incident(*, provider: str, reason: str) -> bool:
    if reason not in PAYMENT_WEBHOOK_ALERTABLE_REASONS:
        return False

    return record_threshold_incident(
        counter_key=_get_payment_webhook_failure_counter_key(provider=provider, reason=reason),
        threshold=settings.PAYMENT_WEBHOOK_INCIDENT_THRESHOLD,
        window_seconds=settings.PAYMENT_WEBHOOK_INCIDENT_WINDOW_SECONDS,
        incident=ThresholdIncidentSpec(
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
            },
        ),
    )


def record_payment_status_sync_failure_incident(*, provider: str, error_type: str) -> bool:
    return record_threshold_incident(
        counter_key=_get_payment_status_sync_failure_counter_key(provider=provider),
        threshold=settings.PAYMENT_STATUS_SYNC_INCIDENT_THRESHOLD,
        window_seconds=settings.PAYMENT_STATUS_SYNC_INCIDENT_WINDOW_SECONDS,
        incident=ThresholdIncidentSpec(
            key=PAYMENT_STATUS_SYNC_FAILURE_INCIDENT_KEY,
            title="Repeated invoice status sync failures",
            recovery_title="Invoice status sync recovered",
            severity="critical",
            fingerprint=_build_payment_status_sync_failure_fingerprint(provider=provider),
            details={
                "provider": provider,
                "error_type": error_type,
            },
        ),
    )


def resolve_payment_status_sync_failure_incident(*, provider: str) -> bool:
    return resolve_threshold_incident(
        incident=ThresholdIncidentSpec(
            key=PAYMENT_STATUS_SYNC_FAILURE_INCIDENT_KEY,
            title="Repeated invoice status sync failures",
            recovery_title="Invoice status sync recovered",
            severity="critical",
            fingerprint=_build_payment_status_sync_failure_fingerprint(provider=provider),
            details={
                "provider": provider,
            },
        ),
    )
