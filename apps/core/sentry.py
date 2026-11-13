from __future__ import annotations

import logging
from typing import Any

from libs.express_pay.redaction import mask_secret_query

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


def _mask_sentry_exception_values(exception: dict[str, Any]) -> None:
    for entry in exception.get("values", []):
        value = entry.get("value")
        if isinstance(value, str):
            entry["value"] = mask_secret_query(value)


def _mask_sentry_event_message(event: dict[str, Any]) -> None:
    message = event.get("message")
    if isinstance(message, str):
        event["message"] = mask_secret_query(message)
        return
    if isinstance(message, dict):
        formatted = message.get("formatted")
        if isinstance(formatted, str):
            message["formatted"] = mask_secret_query(formatted)


def _mask_sentry_event(event: dict[str, Any]) -> dict[str, Any]:
    exception = event.get("exception")
    if isinstance(exception, dict):
        _mask_sentry_exception_values(exception)
    _mask_sentry_event_message(event)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        log_message = logentry.get("message")
        if isinstance(log_message, str):
            logentry["message"] = mask_secret_query(log_message)
    return event


def _mask_sentry_breadcrumb(crumb: dict[str, Any]) -> dict[str, Any]:
    data = crumb.get("data")
    if isinstance(data, dict):
        for key in ("url", "http.query"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = mask_secret_query(value)

    message = crumb.get("message")
    if isinstance(message, str):
        crumb["message"] = mask_secret_query(message)

    return crumb


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    return _mask_sentry_event(event)


def _before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    return _mask_sentry_breadcrumb(crumb)


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
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
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
