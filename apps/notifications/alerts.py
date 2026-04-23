from __future__ import annotations

from django.conf import settings

from apps.core.alerts import ThresholdIncidentSpec, record_threshold_incident, resolve_threshold_incident
from apps.notifications.types import NotificationType

NOTIFICATION_OUTBOX_FAILURE_INCIDENT_KEY = "notifications.outbox.failures"

CRITICAL_NOTIFICATION_TYPES = {
    NotificationType.GUEST_ORDER_DOWNLOAD,
    NotificationType.CUSTOM_GAME_DOWNLOAD,
}


def _build_notification_outbox_failure_counter_key(*, notification_type: str, channel: str) -> str:
    return f"notification-outbox-failure:{channel}:{notification_type}"


def _build_notification_outbox_failure_fingerprint(*, notification_type: str, channel: str) -> str:
    return f"{NOTIFICATION_OUTBOX_FAILURE_INCIDENT_KEY}:{channel}:{notification_type}"


def record_notification_outbox_failure_incident(*, notification_type: str, channel: str) -> bool:
    if notification_type not in CRITICAL_NOTIFICATION_TYPES:
        return False

    return record_threshold_incident(
        counter_key=_build_notification_outbox_failure_counter_key(
            notification_type=notification_type,
            channel=channel,
        ),
        threshold=settings.NOTIFICATION_OUTBOX_INCIDENT_THRESHOLD,
        window_seconds=settings.NOTIFICATION_OUTBOX_INCIDENT_WINDOW_SECONDS,
        incident=ThresholdIncidentSpec(
            key=NOTIFICATION_OUTBOX_FAILURE_INCIDENT_KEY,
            title="Repeated critical notification outbox failures",
            recovery_title="Critical notification outbox recovered",
            severity="critical",
            fingerprint=_build_notification_outbox_failure_fingerprint(
                notification_type=notification_type,
                channel=channel,
            ),
            details={
                "notification_type": notification_type,
                "channel": channel,
            },
        ),
    )


def resolve_notification_outbox_failure_incident(*, notification_type: str, channel: str) -> bool:
    if notification_type not in CRITICAL_NOTIFICATION_TYPES:
        return False

    return resolve_threshold_incident(
        incident=ThresholdIncidentSpec(
            key=NOTIFICATION_OUTBOX_FAILURE_INCIDENT_KEY,
            title="Repeated critical notification outbox failures",
            recovery_title="Critical notification outbox recovered",
            severity="critical",
            fingerprint=_build_notification_outbox_failure_fingerprint(
                notification_type=notification_type,
                channel=channel,
            ),
            details={
                "notification_type": notification_type,
                "channel": channel,
            },
        ),
    )
