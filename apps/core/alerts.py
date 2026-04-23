from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from django.conf import settings
from django.core.cache import cache

from apps.notifications.destinations import TelegramDestination
from apps.notifications.telegram import get_telegram_destination_skip_reason, send_telegram_message

logger = logging.getLogger("apps.alerts")

IncidentSeverity = Literal["critical", "warning"]

DEFAULT_ALERT_DEDUPE_TTL_SECONDS = 60 * 15


@dataclass(frozen=True)
class ThresholdIncidentSpec:
    key: str
    title: str
    recovery_title: str | None = None
    severity: IncidentSeverity = "critical"
    fingerprint: str | None = None
    details: dict[str, Any] | None = None


def _get_incident_alert_dedup_ttl_seconds() -> int:
    configured = getattr(settings, "INCIDENT_ALERT_DEDUPE_TTL_SECONDS", DEFAULT_ALERT_DEDUPE_TTL_SECONDS)
    return max(int(configured), 1)


def _normalize_incident_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _build_incident_message(
    *,
    key: str,
    title: str,
    severity: IncidentSeverity,
    details: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"[{severity.upper()}] {title}",
        f"key: {key}",
    ]
    if details:
        for details_key, details_value in details.items():
            lines.append(f"{details_key}: {_normalize_incident_value(details_value)}")

    return "\n".join(lines)


def _build_alert_cache_key(*, fingerprint: str) -> str:
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return f"incident-alert:{digest}"


def _build_active_incident_cache_key(*, fingerprint: str) -> str:
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return f"incident-active:{digest}"


def _build_default_fingerprint(*, key: str) -> str:
    return key


def _get_incident_fingerprint(*, key: str, fingerprint: str | None) -> str:
    return fingerprint or _build_default_fingerprint(key=key)


def record_threshold_incident(
    *,
    counter_key: str,
    threshold: int,
    window_seconds: int,
    incident: ThresholdIncidentSpec,
) -> bool:
    if threshold <= 0:
        raise ValueError("threshold must be greater than zero")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero")

    if cache.add(counter_key, 1, timeout=window_seconds):
        failures = 1
    else:
        failures = cache.incr(counter_key)

    if failures != threshold:
        return False

    alert_details = {
        **(incident.details or {}),
        "failures": failures,
        "window_minutes": window_seconds // 60,
    }
    sent = send_incident_alert(
        key=incident.key,
        title=incident.title,
        severity=incident.severity,
        fingerprint=incident.fingerprint,
        details=alert_details,
    )
    if sent:
        cache.set(
            _build_active_incident_cache_key(
                fingerprint=_get_incident_fingerprint(key=incident.key, fingerprint=incident.fingerprint),
            ),
            "1",
            timeout=None,
        )
    return sent


def resolve_threshold_incident(
    *,
    incident: ThresholdIncidentSpec,
    details: dict[str, Any] | None = None,
) -> bool:
    if incident.recovery_title is None:
        raise ValueError("incident recovery_title must be configured")

    resolved_fingerprint = _get_incident_fingerprint(key=incident.key, fingerprint=incident.fingerprint)
    active_cache_key = _build_active_incident_cache_key(fingerprint=resolved_fingerprint)
    if not cache.get(active_cache_key):
        return False

    sent = send_incident_recovery(
        key=incident.key,
        title=incident.recovery_title,
        fingerprint=resolved_fingerprint,
        details={**(incident.details or {}), **(details or {})},
    )
    if not sent:
        return False

    cache.delete(active_cache_key)
    return True


def send_incident_alert(
    *,
    key: str,
    title: str,
    severity: IncidentSeverity = "critical",
    details: dict[str, Any] | None = None,
    fingerprint: str | None = None,
    dedupe_ttl_seconds: int | None = None,
) -> bool:
    reason = get_telegram_destination_skip_reason(TelegramDestination.INCIDENTS)
    if reason is not None:
        logger.warning(
            "incident alert skipped: telegram incidents route is not configured",
            extra={
                "event": "incident_alert.skipped",
                "alert_key": key,
                "severity": severity,
                "reason": reason,
            },
        )
        return False

    resolved_fingerprint = _get_incident_fingerprint(key=key, fingerprint=fingerprint)
    resolved_dedup_ttl_seconds = dedupe_ttl_seconds or _get_incident_alert_dedup_ttl_seconds()
    cache_key = _build_alert_cache_key(fingerprint=resolved_fingerprint)

    if not cache.add(cache_key, "1", timeout=resolved_dedup_ttl_seconds):
        logger.info(
            "incident alert skipped: duplicate",
            extra={
                "event": "incident_alert.skipped",
                "alert_key": key,
                "severity": severity,
                "fingerprint": resolved_fingerprint,
                "reason": "duplicate",
                "dedup_ttl_seconds": resolved_dedup_ttl_seconds,
            },
        )
        return False

    text = _build_incident_message(key=key, title=title, severity=severity, details=details)
    send_telegram_message(destination=TelegramDestination.INCIDENTS, text=text)
    logger.error(
        "incident alert sent",
        extra={
            "event": "incident_alert.sent",
            "alert_key": key,
            "severity": severity,
            "fingerprint": resolved_fingerprint,
            "dedup_ttl_seconds": resolved_dedup_ttl_seconds,
        },
    )
    return True


def send_incident_recovery(
    *,
    key: str,
    title: str,
    fingerprint: str | None = None,
    details: dict[str, Any] | None = None,
) -> bool:
    reason = get_telegram_destination_skip_reason(TelegramDestination.INCIDENTS)
    if reason is not None:
        logger.warning(
            "incident recovery skipped: telegram incidents route is not configured",
            extra={
                "event": "incident_recovery.skipped",
                "alert_key": key,
                "reason": reason,
            },
        )
        return False

    resolved_fingerprint = _get_incident_fingerprint(key=key, fingerprint=fingerprint)
    text = _build_incident_message(
        key=key,
        title=title,
        severity="warning",
        details=details,
    ).replace("[WARNING]", "[RESOLVED]", 1)
    send_telegram_message(destination=TelegramDestination.INCIDENTS, text=text)
    logger.info(
        "incident recovery sent",
        extra={
            "event": "incident_recovery.sent",
            "alert_key": key,
            "fingerprint": resolved_fingerprint,
        },
    )
    return True
