from __future__ import annotations

from apps.notifications.models import NotificationOutbox
from apps.notifications.services import (
    cleanup_old_notification_outboxes,
    create_notification_outbox,
    decrypt_outbox_payload as notification_decrypt_outbox_payload,
    encrypt_outbox_payload as notification_encrypt_outbox_payload,
    process_notification_outbox,
)
from apps.notifications.types import NotificationType


class GuestAccessEmailOutboxError(Exception):
    pass


class GuestAccessEmailOutboxPayloadError(GuestAccessEmailOutboxError):
    pass


def encrypt_outbox_payload(payload: list[dict]) -> bytes:
    return notification_encrypt_outbox_payload(payload)


def decrypt_outbox_payload(payload_encrypted: bytes) -> list[dict]:
    return notification_decrypt_outbox_payload(payload_encrypted)


def create_guest_access_email_outbox(*, order, guest_access_payloads: list[dict]) -> NotificationOutbox:
    return create_notification_outbox(
        notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
        recipient=order.email,
        payload=guest_access_payloads,
        target=order,
    )


def process_guest_access_email_outbox(*, outbox_id: int) -> bool:
    return process_notification_outbox(outbox_id=outbox_id)


def cleanup_old_guest_access_email_outboxes(
    *,
    sent_retention_days: int | None = None,
    failed_retention_days: int | None = None,
) -> dict[str, int]:
    return cleanup_old_notification_outboxes(
        notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
        sent_retention_days=sent_retention_days,
        failed_retention_days=failed_retention_days,
    )
