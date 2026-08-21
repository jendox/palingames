from __future__ import annotations

import logging
from datetime import date

from django.contrib.contenttypes.models import ContentType

from apps.core.logging import log_event
from apps.notifications.destinations import TelegramDestination
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import enqueue_email_notification, enqueue_telegram_notification
from apps.notifications.telegram import get_telegram_destination_skip_reason
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


def _monthly_report_recipient(*, period_start: date, part_index: int, parts_total: int) -> str:
    return f"npd_monthly_report:{period_start:%Y-%m}:{part_index}/{parts_total}"


ACTIVE_STATUSES = [
    NotificationOutbox.Status.PENDING,
    NotificationOutbox.Status.PROCESSING,
    NotificationOutbox.Status.DELIVERING,
    NotificationOutbox.Status.SENT,
]


def _has_active_monthly_report_part(*, period_start: date, part_index: int, parts_total: int) -> bool:
    return NotificationOutbox.objects.filter(
        notification_type=NotificationType.PAYMENTS_MONTHLY_REPORT_ADMIN,
        channel=NotificationOutbox.Channel.TELEGRAM,
        recipient=_monthly_report_recipient(
            period_start=period_start,
            part_index=part_index,
            parts_total=parts_total,
        ),
        status__in=ACTIVE_STATUSES,
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


def notify_payments_monthly_report_admin_telegram(
    *,
    period_start: date,
    period_end: date,
    report_text_parts: list[str],
) -> dict[str, int]:
    parts_total = len(report_text_parts)
    result: dict[str, int] = {"enqueued": 0, "skipped": 0}
    reason: str | None = get_telegram_destination_skip_reason(TelegramDestination.NOTIFICATIONS)
    if reason is not None:
        log_event(
            logger,
            logging.WARNING,
            "payments_monthly_report_admin_telegram.enqueue_skipped",
            reason=reason,
            parts_total=parts_total,
        )
        result["skipped"] = parts_total
        return result

    failed = False
    for part_index, part in enumerate(report_text_parts, start=1):
        if _has_active_monthly_report_part(
            period_start=period_start,
            part_index=part_index,
            parts_total=parts_total,
        ):
            log_event(
                logger,
                logging.INFO,
                "payments_monthly_report_admin_telegram.enqueue_skipped",
                reason="already_enqueued_or_sent",
                part_index=part_index,
            )
            result.update({"skipped": result["skipped"] + 1})
            continue
        try:
            enqueue_telegram_notification(
                notification_type=NotificationType.PAYMENTS_MONTHLY_REPORT_ADMIN,
                destination=TelegramDestination.NOTIFICATIONS,
                recipient=_monthly_report_recipient(
                    period_start=period_start,
                    part_index=part_index,
                    parts_total=parts_total,
                ),
                payload={
                    "report_text": part,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "part_index": part_index,
                    "parts_total": parts_total,
                },
                target=None,
            )
            log_event(
                logger,
                logging.INFO,
                "payments_monthly_report_admin_telegram.enqueued",
                part_index=part_index,
            )
            result.update({"enqueued": result["enqueued"] + 1})
        except Exception:
            failed = True
            log_event(
                logger,
                logging.ERROR,
                "payments_monthly_report_admin_telegram.enqueue_failed",
                exc_info=True,
                part_index=part_index,
                parts_total=len(report_text_parts),
            )
    if failed:
        raise RuntimeError("payments monthly report enqueue incomplete")
    return result
