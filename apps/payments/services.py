import logging

from django.db import transaction
from django.utils import timezone

from apps.access.services import (
    create_guest_access_email_outbox_for_order,
    grant_user_product_accesses,
)
from apps.access.tasks import send_guest_access_email_outbox_task
from apps.core.logging import log_event
from apps.core.metrics import inc_order_paid, inc_order_paid_duplicate
from apps.orders.models import Order
from libs.payments.models import InvoiceStatus

from .models import Invoice

logger = logging.getLogger("apps.payments")


def map_invoice_status(provider_status: int | None) -> str | None:
    if provider_status is None:
        return None

    mapping = {
        InvoiceStatus.PENDING: Invoice.InvoiceStatus.PENDING,
        InvoiceStatus.EXPIRED: Invoice.InvoiceStatus.EXPIRED,
        InvoiceStatus.PAID: Invoice.InvoiceStatus.PAID,
        InvoiceStatus.CANCELED: Invoice.InvoiceStatus.CANCELED,
        InvoiceStatus.REFUNDED: Invoice.InvoiceStatus.REFUNDED,
    }
    try:
        return mapping.get(InvoiceStatus(provider_status))
    except ValueError:
        return None


def normalize_notification_datetime(value):
    if value and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def mark_order_paid(
    order: Order,
    invoice: Invoice,
    *,
    paid_at=None,
    source: str,
    persist: bool = True,
) -> None:
    paid_at = paid_at or timezone.now()
    already_paid = order.status == Order.OrderStatus.PAID

    invoice.status = Invoice.InvoiceStatus.PAID
    invoice.paid_at = paid_at
    invoice.cancelled_at = None

    order.status = Order.OrderStatus.PAID
    order.paid_at = paid_at
    order.cancelled_at = None
    order.failure_reason = None

    if persist:
        invoice.save(
            update_fields=[
                "status",
                "paid_at",
                "cancelled_at",
                "updated_at",
            ],
        )
        order.save(
            update_fields=[
                "status",
                "paid_at",
                "cancelled_at",
                "failure_reason",
                "updated_at",
            ],
        )

    if not already_paid:
        guest_access_payloads = []
        guest_access_email_outbox = None
        if order.checkout_type == Order.CheckoutType.AUTHENTICATED:
            grant_user_product_accesses(order)
        elif order.checkout_type == Order.CheckoutType.GUEST:
            guest_access_email_outbox = create_guest_access_email_outbox_for_order(order)
            if guest_access_email_outbox:
                guest_access_payloads = [{}] * order.items_count
                transaction.on_commit(
                    lambda: send_guest_access_email_outbox_task.delay(guest_access_email_outbox.id),
                )
        log_event(
            logger,
            logging.INFO,
            "order.paid",
            source=source,
            order_id=order.id,
            order_public_id=str(order.public_id),
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            checkout_type=order.checkout_type,
            user_id=order.user_id,
            guest_accesses_count=len(guest_access_payloads),
            guest_access_email_outbox_id=guest_access_email_outbox.id if guest_access_email_outbox else None,
        )
        inc_order_paid(checkout_type=order.checkout_type, source=order.source)
    else:
        log_event(
            logger,
            logging.INFO,
            "order.paid.duplicate",
            source=source,
            order_id=order.id,
            order_public_id=str(order.public_id),
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
            checkout_type=order.checkout_type,
            user_id=order.user_id,
        )
        inc_order_paid_duplicate(checkout_type=order.checkout_type, source=order.source)


def apply_order_status_from_invoice_status(
    invoice: Invoice,
    normalized_status: str | None,
    event_at,
    *,
    source: str,
) -> None:
    if normalized_status == Invoice.InvoiceStatus.PAID:
        mark_order_paid(
            invoice.order,
            invoice,
            paid_at=event_at,
            source=source,
            persist=False,
        )
        return

    if normalized_status == Invoice.InvoiceStatus.CANCELED:
        invoice.paid_at = None
        invoice.cancelled_at = event_at or timezone.now()
        invoice.order.status = Order.OrderStatus.CANCELED
        invoice.order.paid_at = None
        invoice.order.cancelled_at = invoice.cancelled_at
        invoice.order.failure_reason = None
        return

    if normalized_status == Invoice.InvoiceStatus.EXPIRED:
        invoice.paid_at = None
        invoice.cancelled_at = None
        invoice.order.status = Order.OrderStatus.FAILED
        invoice.order.paid_at = None
        invoice.order.cancelled_at = None
        invoice.order.failure_reason = "invoice_expired"
        return

    if normalized_status == Invoice.InvoiceStatus.PENDING:
        invoice.paid_at = None
        invoice.cancelled_at = None
        invoice.order.status = Order.OrderStatus.WAITING_FOR_PAYMENT
        invoice.order.paid_at = None
        invoice.order.cancelled_at = None
        invoice.order.failure_reason = None


def apply_invoice_status_update(
    invoice: Invoice,
    *,
    provider_status: int | None,
    source: str,
    persist: bool = True,
    status_payload: dict | None = None,
) -> str | None:
    status_payload = status_payload or {}
    normalized_status = map_invoice_status(provider_status)
    normalized_event_at = normalize_notification_datetime(status_payload.get("event_at"))

    invoice.provider = invoice.provider or Invoice._meta.get_field("provider").default
    invoice.last_status_check_at = timezone.now()
    if "raw_response" in status_payload:
        invoice.raw_last_status_response = status_payload["raw_response"]
    if status_payload.get("amount") is not None:
        invoice.amount = status_payload["amount"]
    if status_payload.get("currency") is not None:
        invoice.currency = status_payload["currency"]
    if normalized_status is not None:
        invoice.status = normalized_status

    apply_order_status_from_invoice_status(
        invoice,
        normalized_status,
        normalized_event_at,
        source=source,
    )

    if persist:
        invoice.save(
            update_fields=[
                "provider",
                "status",
                "amount",
                "currency",
                "paid_at",
                "cancelled_at",
                "last_status_check_at",
                "raw_last_status_response",
                "updated_at",
            ],
        )
        invoice.order.save(
            update_fields=[
                "status",
                "paid_at",
                "cancelled_at",
                "failure_reason",
                "updated_at",
            ],
        )

    return normalized_status
