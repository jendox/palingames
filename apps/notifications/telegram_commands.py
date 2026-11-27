"""Bot -> Django channel: commands published by telegram-bot to a Redis stream."""

from __future__ import annotations

import logging
import time

from django.conf import settings
from redis.exceptions import ResponseError

from apps.core.logging import log_event
from apps.notifications.telegram import get_telegram_redis_client

logger = logging.getLogger("apps.notifications.telegram_commands")

CONSUMER_NAME = "django-celery"
COMMAND_INVOICE_REPORT = "invoice_report"
# Pending (failed) entries are retried until they are older than this, then dropped.
PENDING_MAX_AGE_SEC = 600


def ensure_telegram_command_consumer_group() -> None:
    client = get_telegram_redis_client()
    try:
        client.xgroup_create(
            settings.TELEGRAM_COMMAND_STREAM,
            settings.TELEGRAM_COMMAND_CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _read_entries(*, start_id: str, count: int) -> list[tuple[str, dict[str, str]]]:
    client = get_telegram_redis_client()
    results = client.xreadgroup(
        groupname=settings.TELEGRAM_COMMAND_CONSUMER_GROUP,
        consumername=CONSUMER_NAME,
        streams={settings.TELEGRAM_COMMAND_STREAM: start_id},
        count=count,
    )
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream_name, messages in results or []:
        entries.extend(messages)
    return entries


def _ack(stream_id: str) -> None:
    get_telegram_redis_client().xack(
        settings.TELEGRAM_COMMAND_STREAM,
        settings.TELEGRAM_COMMAND_CONSUMER_GROUP,
        stream_id,
    )


def _entry_age_sec(stream_id: str) -> float:
    try:
        return time.time() - int(stream_id.split("-", 1)[0]) / 1000
    except ValueError:
        return 0.0


def _handle_entry(*, stream_id: str, fields: dict[str, str]) -> bool:
    """Returns True when the entry may be acknowledged."""
    command = fields.get("command", "")
    period = fields.get("period", "")
    requested_by_id = fields.get("requested_by_id", "")
    correlation_id = fields.get("correlation_id", "")

    if command != COMMAND_INVOICE_REPORT:
        log_event(
            logger,
            logging.WARNING,
            "telegram.command.unknown",
            stream_id=stream_id,
            command=command,
            requested_by_id=requested_by_id,
        )
        return True

    from apps.payments.invoice_report import ReportPeriod, request_invoice_period_report

    try:
        report_period = ReportPeriod(period)
    except ValueError:
        log_event(
            logger,
            logging.WARNING,
            "telegram.command.invalid_period",
            stream_id=stream_id,
            period=period,
            requested_by_id=requested_by_id,
        )
        return True

    try:
        request_invoice_period_report(report_period)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "telegram.command.invoice_report.failed",
            exc_info=True,
            stream_id=stream_id,
            period=period,
            requested_by_id=requested_by_id,
            correlation_id=correlation_id,
        )
        if _entry_age_sec(stream_id) > PENDING_MAX_AGE_SEC:
            log_event(
                logger,
                logging.ERROR,
                "telegram.command.invoice_report.dropped",
                stream_id=stream_id,
                period=period,
                requested_by_id=requested_by_id,
            )
            return True
        return False

    log_event(
        logger,
        logging.INFO,
        "telegram.command.invoice_report.processed",
        stream_id=stream_id,
        period=period,
        requested_by_id=requested_by_id,
        correlation_id=correlation_id,
    )
    return True


def process_telegram_command_stream(*, batch_size: int = 20) -> dict[str, int]:
    ensure_telegram_command_consumer_group()

    processed = 0
    failed = 0
    # "0" re-reads this consumer's unacknowledged entries (retry), ">" reads new ones.
    entries = _read_entries(start_id="0", count=batch_size)
    entries += _read_entries(start_id=">", count=batch_size)
    for stream_id, fields in entries:
        if _handle_entry(stream_id=stream_id, fields=fields):
            _ack(stream_id)
            processed += 1
        else:
            failed += 1

    return {"processed": processed, "failed": failed}
