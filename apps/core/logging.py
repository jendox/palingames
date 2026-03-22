from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_logging_context: ContextVar[dict[str, Any] | None] = ContextVar("logging_context", default=None)

SENSITIVE_KEYS = {
    "address",
    "authorization",
    "cookie",
    "csrfmiddlewaretoken",
    "email",
    "first_name",
    "last_name",
    "password",
    "password1",
    "password2",
    "patronymic",
    "phone",
    "secret",
    "session",
    "sessionid",
    "signature",
    "sms_phone",
    "surname",
    "token",
}
REDACTED = "***"


def generate_request_id() -> str:
    return uuid4().hex


def get_logging_context() -> dict[str, Any]:
    return dict(_logging_context.get() or {})


def set_logging_context(**context: Any) -> None:
    current = get_logging_context()
    current.update({key: value for key, value in context.items() if value is not None})
    _logging_context.set(current)


def clear_logging_context() -> None:
    _logging_context.set({})


def get_request_id() -> str | None:
    request_id = get_logging_context().get("request_id")
    return str(request_id) if request_id else None


def build_task_logging_headers() -> dict[str, str]:
    context = get_logging_context()
    headers: dict[str, str] = {}
    request_id = context.get("request_id")
    if request_id:
        headers["request_id"] = str(request_id)
    return headers


def bind_task_logging_context(task_id: str | None, task_name: str | None, headers: Any) -> None:
    context: dict[str, Any] = {
        "task_id": task_id,
        "task_name": task_name,
    }
    if isinstance(headers, dict):
        request_id = headers.get("request_id")
        if request_id:
            context["request_id"] = str(request_id)
    set_logging_context(**context)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower()
    if normalized_key in SENSITIVE_KEYS:
        return REDACTED

    if isinstance(value, dict):
        return {item_key: redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]

    return value


class LoggingContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = redact_value(get_logging_context())
        record.request_id = context.get("request_id")
        record.logging_context = context
        if not hasattr(record, "event"):
            record.event = record.getMessage()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        context = getattr(record, "logging_context", {})
        event_context = redact_value(getattr(record, "event_context", {}))
        merged_context = {**context, **event_context}
        if merged_context:
            payload["context"] = merged_context

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    /,
    message: str | None = None,
    exc_info: Any = None,
    **context: Any,
) -> None:
    logger.log(
        level,
        message or event,
        exc_info=exc_info,
        extra={
            "event": event,
            "event_context": redact_value(context),
        },
    )
