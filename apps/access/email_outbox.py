from __future__ import annotations

import json
import logging
from datetime import timedelta
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.logging import log_event

from .emails import send_guest_order_download_email
from .models import GuestAccessEmailOutbox

logger = logging.getLogger("apps.access.email_outbox")
MAX_LAST_ERROR_LENGTH = 512


class GuestAccessEmailOutboxError(Exception):
    pass


class GuestAccessEmailOutboxPayloadError(GuestAccessEmailOutboxError):
    pass


@lru_cache(maxsize=4)
def _build_app_data_cipher(app_data_encryption_key: str) -> Fernet:
    return Fernet(app_data_encryption_key.encode("ascii"))


def get_app_data_cipher() -> Fernet:
    return _build_app_data_cipher(settings.APP_DATA_ENCRYPTION_KEY)


def encrypt_outbox_payload(payload: list[dict]) -> bytes:
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return get_app_data_cipher().encrypt(payload_bytes)


def decrypt_outbox_payload(payload_encrypted: bytes) -> list[dict]:
    try:
        payload_bytes = get_app_data_cipher().decrypt(bytes(payload_encrypted))
    except InvalidToken as exc:
        raise GuestAccessEmailOutboxPayloadError("Invalid encrypted outbox payload") from exc
    return json.loads(payload_bytes.decode("utf-8"))


def create_guest_access_email_outbox(*, order, guest_access_payloads: list[dict]) -> GuestAccessEmailOutbox:
    outbox = GuestAccessEmailOutbox.objects.create(
        order=order,
        email=order.email,
        payload_encrypted=encrypt_outbox_payload(guest_access_payloads),
        status=GuestAccessEmailOutbox.GuestAccessEmailStatus.PENDING,
    )
    log_event(
        logger,
        logging.INFO,
        "guest_access.email_outbox.created",
        outbox_id=outbox.id,
        order_id=order.id,
        order_public_id=str(order.public_id),
        email=order.email,
        items_count=len(guest_access_payloads),
    )
    return outbox


def _truncate_error(error: Exception) -> str:
    return str(error)[:MAX_LAST_ERROR_LENGTH]


def process_guest_access_email_outbox(*, outbox_id: int) -> bool:
    with transaction.atomic():
        outbox = (
            GuestAccessEmailOutbox.objects.select_for_update()
            .select_related("order")
            .get(pk=outbox_id)
        )
        if outbox.status == GuestAccessEmailOutbox.GuestAccessEmailStatus.SENT:
            log_event(
                logger,
                logging.INFO,
                "guest_access.email_outbox.skipped",
                outbox_id=outbox.id,
                order_id=outbox.order_id,
                reason="already_sent",
            )
            return False

        outbox.status = GuestAccessEmailOutbox.GuestAccessEmailStatus.PROCESSING
        outbox.attempts += 1
        outbox.last_attempt_at = timezone.now()
        outbox.last_error = ""
        outbox.save(update_fields=["status", "attempts", "last_attempt_at", "last_error", "updated_at"])

    try:
        payload = decrypt_outbox_payload(outbox.payload_encrypted)
        send_guest_order_download_email(
            order=outbox.order,
            guest_access_payloads=payload,
        )
    except Exception as exc:
        with transaction.atomic():
            outbox = GuestAccessEmailOutbox.objects.select_for_update().get(pk=outbox_id)
            outbox.status = GuestAccessEmailOutbox.GuestAccessEmailStatus.FAILED
            outbox.last_error = _truncate_error(exc)
            outbox.save(update_fields=["status", "last_error", "updated_at"])
        log_event(
            logger,
            logging.ERROR,
            "guest_access.email_outbox.failed",
            exc_info=exc,
            outbox_id=outbox_id,
            order_id=outbox.order_id,
            email=outbox.email,
            attempts=outbox.attempts,
            error_type=type(exc).__name__,
        )
        raise

    with transaction.atomic():
        outbox = GuestAccessEmailOutbox.objects.select_for_update().get(pk=outbox_id)
        outbox.status = GuestAccessEmailOutbox.GuestAccessEmailStatus.SENT
        outbox.sent_at = timezone.now()
        outbox.last_error = ""
        outbox.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
    log_event(
        logger,
        logging.INFO,
        "guest_access.email_outbox.sent",
        outbox_id=outbox_id,
        order_id=outbox.order_id,
        email=outbox.email,
        attempts=outbox.attempts,
    )
    return True


def cleanup_old_guest_access_email_outboxes(
    *,
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

    sent_cutoff = now - timedelta(days=sent_retention_days)
    failed_cutoff = now - timedelta(days=failed_retention_days)

    sent_deleted, _ = GuestAccessEmailOutbox.objects.filter(
        status=GuestAccessEmailOutbox.GuestAccessEmailStatus.SENT,
        sent_at__lt=sent_cutoff,
    ).delete()
    failed_deleted, _ = GuestAccessEmailOutbox.objects.filter(
        status=GuestAccessEmailOutbox.GuestAccessEmailStatus.FAILED,
        updated_at__lt=failed_cutoff,
    ).delete()

    log_event(
        logger,
        logging.INFO,
        "guest_access.email_outbox.cleanup.completed",
        sent_deleted=sent_deleted,
        failed_deleted=failed_deleted,
        sent_retention_days=sent_retention_days,
        failed_retention_days=failed_retention_days,
    )
    return {
        "sent_deleted": sent_deleted,
        "failed_deleted": failed_deleted,
    }
