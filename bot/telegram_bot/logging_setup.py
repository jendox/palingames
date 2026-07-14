import json
import logging
from datetime import UTC, datetime
from typing import Any

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
    "telegram_bot_token",
    "bot_token",
}
REDACTED = "***"


def redact_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").lower()
    if normalized_key in SENSITIVE_KEYS:
        return REDACTED

    if isinstance(value, dict):
        return {item_key: redact_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [redact_value(item) for item in value]

    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        context = redact_value(getattr(record, "event_context", {}))
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def log_event(logger, level, event, /, exc_info=None, **context):
    logger.log(level, "", exc_info=exc_info, extra={"event": event, "event_context": context})


def setup_logging(*, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
