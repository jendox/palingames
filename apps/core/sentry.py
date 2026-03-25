from __future__ import annotations

import logging
from typing import Any

from .logging import get_logging_context, redact_value

logger = logging.getLogger("apps.observability.sentry")
SENTRY_TAG_KEYS = (
    "request_id",
    "task_id",
    "task_name",
    "task_state",
    "http_method",
    "path",
    "status_code",
)

try:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
except ImportError:  # pragma: no cover - optional dependency in local dev
    sentry_sdk = None
    CeleryIntegration = DjangoIntegration = None


def _sentry_available() -> bool:
    return sentry_sdk is not None and DjangoIntegration is not None and CeleryIntegration is not None


def init_sentry(
    *,
    dsn: str,
    environment: str,
    release: str | None = None,
    traces_sample_rate: float = 0.0,
) -> bool:
    if not dsn:
        return False

    if not _sentry_available():
        logger.warning("Sentry DSN configured but sentry-sdk is not installed")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
        ],
    )
    logger.info("Sentry initialized", extra={"event": "observability.sentry.initialized"})
    return True


def configure_sentry_scope(**context: Any) -> None:
    if sentry_sdk is None:
        return

    merged_context = {
        **get_logging_context(),
        **{key: value for key, value in context.items() if value is not None},
    }
    if not merged_context:
        return

    redacted_context = redact_value(merged_context)
    with sentry_sdk.configure_scope() as scope:
        for key in SENTRY_TAG_KEYS:
            value = redacted_context.get(key)
            if value is not None:
                scope.set_tag(key, str(value))

        scope.set_context("logging_context", redacted_context)
