from __future__ import annotations

import json
import logging
from datetime import timedelta
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core.logging import log_event
from apps.core.metrics import inc_guest_email_failed, inc_guest_email_outbox_created, inc_guest_email_sent

from .models import NotificationOutbox
from .types import CUSTOM_GAME_DOWNLOAD, GUEST_ORDER_DOWNLOAD

logger = logging.getLogger("apps.notifications")
MAX_LAST_ERROR_LENGTH = 512


class NotificationOutboxError(Exception):
    pass


class NotificationOutboxPayloadError(NotificationOutboxError):
    pass


@lru_cache(maxsize=4)
def _build_app_data_cipher(app_data_encryption_key: str) -> Fernet:
    return Fernet(app_data_encryption_key.encode("ascii"))


def get_app_data_cipher() -> Fernet:
    return _build_app_data_cipher(settings.APP_DATA_ENCRYPTION_KEY)


def encrypt_outbox_payload(payload: dict[str, Any] | list[dict]) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return get_app_data_cipher().encrypt(payload_bytes)


def decrypt_outbox_payload(payload_encrypted: bytes) -> dict[str, Any] | list[dict]:
    try:
        payload_bytes = get_app_data_cipher().decrypt(bytes(payload_encrypted))
    except InvalidToken as exc:
        raise NotificationOutboxPayloadError("Invalid encrypted notification payload") from exc
    return json.loads(payload_bytes.decode("utf-8"))


def create_notification_outbox(
    *,
    notification_type: str,
    recipient: str,
    payload: dict[str, Any] | list[dict],
    target=None,
    channel: str = NotificationOutbox.Channel.EMAIL,
) -> NotificationOutbox:
    content_type = None
    object_id = None
    if target is not None:
        content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
        object_id = target.pk

    outbox = NotificationOutbox.objects.create(
        channel=channel,
        notification_type=notification_type,
        recipient=recipient,
        payload_encrypted=encrypt_outbox_payload(payload),
        status=NotificationOutbox.Status.PENDING,
        content_type=content_type,
        object_id=object_id,
    )
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.created",
        outbox_id=outbox.id,
        notification_type=notification_type,
        channel=channel,
        recipient=recipient,
        target_model=content_type.model if content_type else None,
        target_id=object_id,
    )
    if notification_type == GUEST_ORDER_DOWNLOAD:
        inc_guest_email_outbox_created()
    return outbox


def enqueue_notification_outbox(outbox: NotificationOutbox) -> NotificationOutbox:
    from .tasks import send_notification_outbox_task

    transaction.on_commit(lambda: send_notification_outbox_task.delay(outbox.id))
    return outbox


def enqueue_notification(
    *,
    notification_type: str,
    recipient: str,
    payload: dict[str, Any] | list[dict],
    target=None,
    channel: str = NotificationOutbox.Channel.EMAIL,
) -> NotificationOutbox:
    outbox = create_notification_outbox(
        notification_type=notification_type,
        recipient=recipient,
        payload=payload,
        target=target,
        channel=channel,
    )
    return enqueue_notification_outbox(outbox)


def _truncate_error(error: Exception) -> str:
    return str(error)[:MAX_LAST_ERROR_LENGTH]


def process_notification_outbox(*, outbox_id: int) -> bool:
    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        if outbox.status == NotificationOutbox.Status.SENT:
            log_event(
                logger,
                logging.INFO,
                "notification.outbox.skipped",
                outbox_id=outbox.id,
                notification_type=outbox.notification_type,
                reason="already_sent",
            )
            return False

        outbox.status = NotificationOutbox.Status.PROCESSING
        outbox.attempts += 1
        outbox.last_attempt_at = timezone.now()
        outbox.last_error = ""
        outbox.save(update_fields=["status", "attempts", "last_attempt_at", "last_error", "updated_at"])

    try:
        payload = decrypt_outbox_payload(outbox.payload_encrypted)
        send_notification(outbox=outbox, payload=payload)
    except Exception as exc:
        with transaction.atomic():
            outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
            outbox.status = NotificationOutbox.Status.FAILED
            outbox.last_error = _truncate_error(exc)
            outbox.save(update_fields=["status", "last_error", "updated_at"])
        log_event(
            logger,
            logging.ERROR,
            "notification.outbox.failed",
            exc_info=exc,
            outbox_id=outbox_id,
            notification_type=outbox.notification_type,
            channel=outbox.channel,
            recipient=outbox.recipient,
            attempts=outbox.attempts,
            error_type=type(exc).__name__,
        )
        if outbox.notification_type == GUEST_ORDER_DOWNLOAD:
            inc_guest_email_failed()
        raise

    with transaction.atomic():
        outbox = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
        outbox.status = NotificationOutbox.Status.SENT
        outbox.sent_at = timezone.now()
        outbox.last_error = ""
        outbox.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
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
    if outbox.notification_type == GUEST_ORDER_DOWNLOAD:
        inc_guest_email_sent()
    return True


def send_notification(*, outbox: NotificationOutbox, payload):
    if outbox.channel != NotificationOutbox.Channel.EMAIL:
        raise NotificationOutboxError(f"Unsupported notification channel: {outbox.channel}")

    if outbox.notification_type == GUEST_ORDER_DOWNLOAD:
        from apps.access.emails import send_guest_order_download_email

        send_guest_order_download_email(order=outbox.target, guest_access_payloads=payload)
        return

    if outbox.notification_type == CUSTOM_GAME_DOWNLOAD:
        from apps.custom_games.emails import send_custom_game_download_email
        from apps.custom_games.models import CustomGameDownloadToken, CustomGameRequest

        custom_game_request = outbox.target or CustomGameRequest.objects.get(pk=payload["custom_game_request_id"])
        download_token = CustomGameDownloadToken.objects.get(pk=payload["download_token_id"])
        send_custom_game_download_email(
            custom_game_request=custom_game_request,
            download_token=download_token,
            raw_token=payload["raw_token"],
        )
        return

    raise NotificationOutboxError(f"Unsupported notification type: {outbox.notification_type}")


def cleanup_old_notification_outboxes(
    *,
    notification_type: str | None = None,
    sent_retention_days: int | None = None,
    failed_retention_days: int | None = None,
) -> dict[str, int]:
    now = timezone.now()
    sent_retention_days = (
        settings.GUEST_ACCESS_EMAIL_OUTBOX_SENT_RETENTION_DAYS
        if sent_retention_days is None
        else sent_retention_days
    )
    failed_retention_days = (
        settings.GUEST_ACCESS_EMAIL_OUTBOX_FAILED_RETENTION_DAYS
        if failed_retention_days is None
        else failed_retention_days
    )

    sent_queryset = NotificationOutbox.objects.filter(
        status=NotificationOutbox.Status.SENT,
        sent_at__lt=now - timedelta(days=sent_retention_days),
    )
    failed_queryset = NotificationOutbox.objects.filter(
        status=NotificationOutbox.Status.FAILED,
        updated_at__lt=now - timedelta(days=failed_retention_days),
    )
    if notification_type is not None:
        sent_queryset = sent_queryset.filter(notification_type=notification_type)
        failed_queryset = failed_queryset.filter(notification_type=notification_type)

    sent_deleted, _ = sent_queryset.delete()
    failed_deleted, _ = failed_queryset.delete()
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.cleanup.completed",
        notification_type=notification_type,
        sent_deleted=sent_deleted,
        failed_deleted=failed_deleted,
        sent_retention_days=sent_retention_days,
        failed_retention_days=failed_retention_days,
    )
    return {
        "sent_deleted": sent_deleted,
        "failed_deleted": failed_deleted,
    }
