from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from apps.core.alerts import (
    ThresholdIncidentSpec,
    record_threshold_incident,
    resolve_threshold_incident,
    send_incident_alert,
)

if TYPE_CHECKING:
    from apps.products.smoke_checks import ProductSmokeCheckProblem

DOWNLOAD_DELIVERY_FAILURE_INCIDENT_KEY = "downloads.delivery.failures"
STORAGE_UNAVAILABLE_INCIDENT_KEY = "storage.s3.unavailable"
PRODUCT_SMOKE_CHECK_INCIDENT_KEY = "products.smoke_check"


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
            recovery_title="Download delivery recovered",
            severity="critical",
            fingerprint=_build_download_delivery_fingerprint(delivery_type=delivery_type, reason=reason),
            details={
                "delivery_type": delivery_type,
                "reason": reason,
            },
        ),
    )


def resolve_download_delivery_failure_incident(*, delivery_type: str, reason: str) -> bool:
    return resolve_threshold_incident(
        incident=ThresholdIncidentSpec(
            key=DOWNLOAD_DELIVERY_FAILURE_INCIDENT_KEY,
            title="Repeated download delivery failures",
            recovery_title="Download delivery recovered",
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
            recovery_title="Storage recovered",
            severity="critical",
            fingerprint=_build_storage_unavailable_fingerprint(operation=operation),
            details={
                "operation": operation,
            },
        ),
    )


def resolve_storage_unavailable_incident(*, operation: str) -> bool:
    return resolve_threshold_incident(
        incident=ThresholdIncidentSpec(
            key=STORAGE_UNAVAILABLE_INCIDENT_KEY,
            title="Storage is unavailable",
            recovery_title="Storage recovered",
            severity="critical",
            fingerprint=_build_storage_unavailable_fingerprint(operation=operation),
            details={
                "operation": operation,
            },
        ),
    )


def build_product_smoke_check_fingerprint(problem: ProductSmokeCheckProblem) -> str:
    parts = [
        PRODUCT_SMOKE_CHECK_INCIDENT_KEY,
        str(problem.product_id),
        problem.code,
    ]
    image_id = problem.details.get("image_id")
    if image_id is not None:
        parts.append(str(image_id))
    file_id = problem.details.get("file_id")
    if file_id is not None:
        parts.append(str(file_id))
    return ":".join(parts)


def alert_product_smoke_check_problem(problem: ProductSmokeCheckProblem) -> bool:
    return send_incident_alert(
        key=PRODUCT_SMOKE_CHECK_INCIDENT_KEY,
        title="Product smoke check failed",
        severity=problem.severity,
        fingerprint=build_product_smoke_check_fingerprint(problem),
        details={
            "product_id": problem.product_id,
            "problem": problem.code,
            **problem.details,
        },
        dedupe_ttl_seconds=settings.PRODUCT_SMOKE_CHECK_ALERT_DEDUPE_TTL_SECONDS,
    )
