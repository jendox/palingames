from __future__ import annotations

import hashlib
import logging
from typing import Any, Literal

from django.conf import settings
from django.core.cache import cache

from apps.notifications.destinations import TelegramDestination
from apps.notifications.telegram import get_telegram_destination_skip_reason, send_telegram_message

logger = logging.getLogger("apps.alerts")

IncidentSeverity = Literal["critical", "warning"]

DEFAULT_ALERT_DEDUPE_TTL_SECONDS = 60 * 15


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


def _build_default_fingerprint(*, key: str) -> str:
    return key


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

    resolved_fingerprint = fingerprint or _build_default_fingerprint(key=key)
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
