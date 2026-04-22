from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.core.cache import cache

from apps.notifications.destinations import TelegramDestination
from apps.notifications.telegram import get_telegram_destination_skip_reason, send_telegram_message

logger = logging.getLogger("apps.alerts")

ALERT_DEDUPE_TTL_SECONDS = 60 * 15


def _build_incident_message(*, title: str, details: dict[str, Any] | None = None) -> str:
    lines = [f"CRITICAL: {title}"]
    if details:
        for key, value in details.items():
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _build_alert_cache_key(*, key: str, details: dict[str, Any] | None = None) -> str:
    payload = {
        "key": key,
        "details": details or {},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8"),
    ).hexdigest()
    return f"incident-alert:{digest}"


def send_incident_alert(
    *,
    key: str,
    title: str,
    details: dict[str, Any] | None = None,
    dedupe_ttl_seconds: int = ALERT_DEDUPE_TTL_SECONDS,
) -> bool:
    reason = get_telegram_destination_skip_reason(TelegramDestination.INCIDENTS)
    if reason is not None:
        logger.warning(
            "incident alert skipped: telegram incidents route is not configured",
            extra={"event": "incident_alert.skipped", "alert_key": key, "reason": reason},
        )
        return False

    cache_key = _build_alert_cache_key(key=key, details=details)
    if not cache.add(cache_key, "1", timeout=dedupe_ttl_seconds):
        logger.info(
            "incident alert skipped: duplicate",
            extra={"event": "incident_alert.skipped", "alert_key": key, "reason": "duplicate"},
        )
        return False

    text = _build_incident_message(title=title, details=details)
    send_telegram_message(destination=TelegramDestination.INCIDENTS, text=text)
    logger.error(
        "incident alert sent",
        extra={"event": "incident_alert.sent", "alert_key": key},
    )
    return True
