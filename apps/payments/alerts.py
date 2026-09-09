from __future__ import annotations

from django.conf import settings

from apps.core.alerts import (
    ThresholdIncidentSpec,
    record_threshold_incident,
    resolve_threshold_incident,
    send_incident_alert,
)

PAYMENT_WEBHOOK_FAILURE_INCIDENT_KEY = "payments.webhook.failures"
PAYMENT_STATUS_SYNC_FAILURE_INCIDENT_KEY = "payments.status_sync.failures"
UNMAPPED_PROVIDER_STATUS_INCIDENT_KEY = "payments.unmapped_provider_status"
ORDER_REFUNDED_INCIDENT_KEY = "payments.order_refunded"

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


def record_order_refunded_incident(*, provider: str, order_id: int, invoice_id: int) -> bool:
    # A refund is terminal, so one alert per order is enough for a long time.
    return send_incident_alert(
        key=ORDER_REFUNDED_INCIDENT_KEY,
        title="Order refunded by payment provider",
        severity="warning",
        fingerprint=f"{ORDER_REFUNDED_INCIDENT_KEY}:{order_id}",
        details={"provider": provider, "order_id": order_id, "invoice_id": invoice_id},
        dedupe_ttl_seconds=settings.ORDER_DELIVERY_ALERT_DEDUPE_TTL_SECONDS,
    )


def record_unmapped_provider_status_incident(*, provider: str, provider_status: int | str) -> bool:
    # Default dedupe TTL on purpose: every unmapped status is a payment the app did not process,
    # so the alert has to keep coming back while the problem lasts.
    return send_incident_alert(
        key=UNMAPPED_PROVIDER_STATUS_INCIDENT_KEY,
        title="Unmapped payment provider status",
        severity="critical",
        fingerprint=f"{UNMAPPED_PROVIDER_STATUS_INCIDENT_KEY}:{provider}:{provider_status}",
        details={"provider": provider, "provider_status": str(provider_status)},
    )
