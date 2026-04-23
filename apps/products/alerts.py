from __future__ import annotations

from apps.core.alerts import ThresholdIncidentSpec, record_threshold_incident

DOWNLOAD_DELIVERY_FAILURE_INCIDENT_KEY = "downloads.delivery.failures"
STORAGE_UNAVAILABLE_INCIDENT_KEY = "storage.s3.unavailable"


def _build_download_delivery_counter_key(*, delivery_type: str, reason: str) -> str:
    return f"download-delivery-failure:{delivery_type}:{reason}"


def _build_download_delivery_fingerprint(*, delivery_type: str, reason: str) -> str:
    return f"{DOWNLOAD_DELIVERY_FAILURE_INCIDENT_KEY}:{delivery_type}:{reason}"


def record_download_delivery_failure_incident(
    *,
    delivery_type: str,
    reason: str,
    threshold: int,
    window_seconds: int,
) -> bool:
    return record_threshold_incident(
        counter_key=_build_download_delivery_counter_key(delivery_type=delivery_type, reason=reason),
        threshold=threshold,
        window_seconds=window_seconds,
        incident=ThresholdIncidentSpec(
            key=DOWNLOAD_DELIVERY_FAILURE_INCIDENT_KEY,
            title="Repeated download delivery failures",
            severity="critical",
            fingerprint=_build_download_delivery_fingerprint(delivery_type=delivery_type, reason=reason),
            details={
                "delivery_type": delivery_type,
                "reason": reason,
            },
        ),
    )


def _build_storage_unavailable_counter_key(*, operation: str) -> str:
    return f"storage-unavailable:{operation}"


def _build_storage_unavailable_fingerprint(*, operation: str) -> str:
    return f"{STORAGE_UNAVAILABLE_INCIDENT_KEY}:{operation}"


def record_storage_unavailable_incident(
    *,
    operation: str,
    threshold: int,
    window_seconds: int,
) -> bool:
    return record_threshold_incident(
        counter_key=_build_storage_unavailable_counter_key(operation=operation),
        threshold=threshold,
        window_seconds=window_seconds,
        incident=ThresholdIncidentSpec(
            key=STORAGE_UNAVAILABLE_INCIDENT_KEY,
            title="Storage is unavailable",
            severity="critical",
            fingerprint=_build_storage_unavailable_fingerprint(operation=operation),
            details={
                "operation": operation,
            },
        ),
    )
