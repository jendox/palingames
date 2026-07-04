from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType

from apps.core.logging import log_event
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import enqueue_email_notification
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.payments.models import Invoice

logger = logging.getLogger("apps.payments.notifications")


def _get_invoice_recipient_email(invoice: Invoice) -> str:
    target = invoice.target
    if target is None:
        return ""
    if isinstance(target, Order):
        return target.email
    return target.contact_email


def _has_pending_invoice_created_user_notification(invoice: Invoice) -> bool:
    content_type = ContentType.objects.get_for_model(invoice, for_concrete_model=False)
    return NotificationOutbox.objects.filter(
        notification_type=NotificationType.INVOICE_CREATED_USER,
        content_type=content_type,
        object_id=invoice.pk,
        status__in=[
            NotificationOutbox.Status.PENDING,
            NotificationOutbox.Status.PROCESSING,
        ],
    ).exists()


def ensure_invoice_created_user_email(invoice: Invoice) -> None:
    provider_invoice_no = (invoice.provider_invoice_no or "").strip()
    if not provider_invoice_no:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.enqueue_skipped",
            invoice_id=invoice.id,
            reason="missing_provider_invoice_no",
        )
        return

    if not invoice.invoice_url:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.enqueue_skipped",
            invoice_id=invoice.id,
            provider_invoice_no=provider_invoice_no,
            reason="missing_invoice_url",
        )
        return

    recipient = _get_invoice_recipient_email(invoice).strip()
    if not recipient:
        log_event(
            logger,
            logging.WARNING,
            "invoice.created_user_email.enqueue_skipped",
            invoice_id=invoice.id,
            provider_invoice_no=provider_invoice_no,
            reason="missing_recipient_email",
        )
        return

    if invoice.payment_email_sent_for_provider_invoice_no == provider_invoice_no:
        log_event(
            logger,
            logging.INFO,
            "invoice.created_user_email.enqueue_skipped",
            invoice_id=invoice.id,
            provider_invoice_no=provider_invoice_no,
            reason="already_sent_for_provider_invoice_no",
        )
        return

    if _has_pending_invoice_created_user_notification(invoice):
        log_event(
            logger,
            logging.INFO,
            "invoice.created_user_email.enqueue_skipped",
            invoice_id=invoice.id,
            provider_invoice_no=provider_invoice_no,
            reason="pending_outbox_exists",
        )
        return

    try:
        enqueue_email_notification(
            notification_type=NotificationType.INVOICE_CREATED_USER,
            recipient=recipient,
            payload={"invoice_id": invoice.id},
            target=invoice,
        )
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "invoice.created_user_email.enqueue_failed",
            exc_info=True,
            invoice_id=invoice.id,
            provider_invoice_no=provider_invoice_no,
            recipient=recipient,
        )
        raise

    log_event(
        logger,
        logging.INFO,
        "invoice.created_user_email.enqueued",
        invoice_id=invoice.id,
        provider_invoice_no=provider_invoice_no,
        recipient=recipient,
        target_kind=invoice.target_kind,
    )
