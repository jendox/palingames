from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from redis.exceptions import ResponseError

from apps.core.logging import log_event
from apps.notifications.models import NotificationOutbox
from apps.notifications.telegram import get_telegram_redis_client

from .alerts import record_notification_outbox_failure_incident, resolve_notification_outbox_failure_incident
from .services import MAX_LAST_ERROR_LENGTH

logger = logging.getLogger("apps.notifications.telegram_delivery")


def _ensure_feedback_consumer_group(*, stream: str) -> None:
    client = get_telegram_redis_client()
    group = settings.TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP
    try:
        client.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def ensure_telegram_feedback_consumer_groups() -> None:
    _ensure_feedback_consumer_group(stream=settings.TELEGRAM_OUTBOUND_ACK_STREAM)
    _ensure_feedback_consumer_group(stream=settings.TELEGRAM_OUTBOUND_FAILED_STREAM)


def _read_feedback_stream(*, stream: str, count: int = 100) -> list[tuple[str, dict[str, str]]]:
    client = get_telegram_redis_client()
    group = settings.TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP
    results = client.xreadgroup(
        groupname=group,
        consumername="django-celery",
        streams={stream: ">"},
        count=count,
        block=0,
    )
    if not results:
        return []

    entries: list[tuple[str, dict[str, str]]] = []
    for _stream_name, messages in results:
        entries.extend(messages)
    return entries


def _ack_feedback_entry(*, stream: str, stream_id: str) -> None:
    client = get_telegram_redis_client()
    client.xack(stream, settings.TELEGRAM_OUTBOUND_FEEDBACK_CONSUMER_GROUP, stream_id)


def confirm_telegram_outbox_delivery(*, outbox_id: int) -> bool:
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        if outbox.status == NotificationOutbox.Status.SENT:
            return False
        if outbox.status != NotificationOutbox.Status.DELIVERING:
            return False

        outbox.status = NotificationOutbox.Status.SENT
        outbox.sent_at = timezone.now()
        outbox.last_error = ""
        outbox.save(update_fields=["status", "sent_at", "last_error", "updated_at"])

    resolve_notification_outbox_failure_incident(
        notification_type=outbox.notification_type,
        channel=outbox.channel,
    )
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.sent",
        outbox_id=outbox_id,
        notification_type=outbox.notification_type,
        channel=outbox.channel,
        recipient=outbox.recipient,
        attempts=outbox.attempts,
    )
    return True


def fail_telegram_outbox_delivery(*, outbox_id: int, error: str) -> bool:
    truncated_error = error[:MAX_LAST_ERROR_LENGTH]

    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        if outbox.status in {NotificationOutbox.Status.SENT, NotificationOutbox.Status.FAILED}:
            return False
        if outbox.status != NotificationOutbox.Status.DELIVERING:
            return False

        outbox.status = NotificationOutbox.Status.FAILED
        outbox.last_error = truncated_error
        outbox.save(update_fields=["status", "last_error", "updated_at"])

    log_event(
        logger,
        logging.ERROR,
        "notification.outbox.failed",
        outbox_id=outbox_id,
        notification_type=outbox.notification_type,
        channel=outbox.channel,
        recipient=outbox.recipient,
        attempts=outbox.attempts,
        error_type="TelegramDeliveryError",
        error=truncated_error,
    )
    record_notification_outbox_failure_incident(
        notification_type=outbox.notification_type,
        channel=outbox.channel,
    )
    return True


def _process_ack_entry(*, stream_id: str, fields: dict[str, str]) -> None:
    source = fields.get("source", "")
    correlation_id = fields.get("correlation_id", "")
    if source != "outbox" or not correlation_id:
        return

    try:
        outbox_id = int(correlation_id)
    except ValueError:
        log_event(
            logger,
            logging.ERROR,
            "telegram.outbound.ack.invalid_correlation_id",
            stream_id=stream_id,
            correlation_id=correlation_id,
        )
        return

    confirmed = confirm_telegram_outbox_delivery(outbox_id=outbox_id)
    if confirmed:
        log_event(
            logger,
            logging.INFO,
            "telegram.outbound.ack.processed",
            stream_id=stream_id,
            outbox_id=outbox_id,
        )


def _process_failed_entry(*, stream_id: str, fields: dict[str, str]) -> None:
    source = fields.get("source", "")
    correlation_id = fields.get("correlation_id", "")
    error = fields.get("error", "")
    if source != "outbox" or not correlation_id:
        return

    try:
        outbox_id = int(correlation_id)
    except ValueError:
        log_event(
            logger,
            logging.ERROR,
            "telegram.outbound.failed.invalid_correlation_id",
            stream_id=stream_id,
            correlation_id=correlation_id,
        )
        return

    failed = fail_telegram_outbox_delivery(outbox_id=outbox_id, error=error or "telegram delivery failed")
    if failed:
        log_event(
            logger,
            logging.INFO,
            "telegram.outbound.failed.processed",
            stream_id=stream_id,
            outbox_id=outbox_id,
        )


def process_telegram_outbound_feedback(*, batch_size: int = 100) -> dict[str, int]:
    ensure_telegram_feedback_consumer_groups()

    ack_processed = 0
    failed_processed = 0

    for stream_id, fields in _read_feedback_stream(
        stream=settings.TELEGRAM_OUTBOUND_ACK_STREAM,
        count=batch_size,
    ):
        _process_ack_entry(stream_id=stream_id, fields=fields)
        _ack_feedback_entry(stream=settings.TELEGRAM_OUTBOUND_ACK_STREAM, stream_id=stream_id)
        ack_processed += 1

    for stream_id, fields in _read_feedback_stream(
        stream=settings.TELEGRAM_OUTBOUND_FAILED_STREAM,
        count=batch_size,
    ):
        _process_failed_entry(stream_id=stream_id, fields=fields)
        _ack_feedback_entry(stream=settings.TELEGRAM_OUTBOUND_FAILED_STREAM, stream_id=stream_id)
        failed_processed += 1

    if ack_processed or failed_processed:
        log_event(
            logger,
            logging.INFO,
            "telegram.outbound.feedback.processed",
            ack_processed=ack_processed,
            failed_processed=failed_processed,
        )

    return {
        "ack_processed": ack_processed,
        "failed_processed": failed_processed,
    }


def reap_stuck_telegram_outbox_deliveries() -> int:
    cutoff = timezone.now() - timezone.timedelta(minutes=settings.TELEGRAM_OUTBOX_DELIVERING_TIMEOUT_MINUTES)
    stuck_outboxes = NotificationOutbox.objects.filter(
        channel=NotificationOutbox.Channel.TELEGRAM,
        status=NotificationOutbox.Status.DELIVERING,
        last_attempt_at__lt=cutoff,
    ).order_by("id")

    reaped = 0
    for outbox in stuck_outboxes:
        if fail_telegram_outbox_delivery(
            outbox_id=outbox.id,
            error="telegram delivery ack timeout",
        ):
            reaped += 1

    if reaped:
        log_event(
            logger,
            logging.WARNING,
            "telegram.outbox.reaper.completed",
            reaped=reaped,
            timeout_minutes=settings.TELEGRAM_OUTBOX_DELIVERING_TIMEOUT_MINUTES,
        )

    return reaped
