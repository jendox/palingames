import logging

from django.db import transaction
from django.utils import timezone

from apps.access.services import (
    create_guest_access_email_outbox_for_order,
    grant_user_product_accesses,
)
from apps.access.tasks import send_guest_access_email_outbox_task
from apps.core.logging import log_event
from apps.orders.models import Order

from .models import Invoice

logger = logging.getLogger("apps.payments")


def mark_order_paid(
    order: Order,
    invoice: Invoice,
    *,
    paid_at=None,
    source: str,
    persist: bool = True,
) -> None:
    paid_at = paid_at or timezone.now()

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
