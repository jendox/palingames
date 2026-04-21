import logging
from time import perf_counter

from django.db import transaction
from django.utils import timezone

from apps.access.services import (
    create_guest_access_notification_for_order,
    grant_user_product_accesses,
)
from apps.core.analytics import send_ga4_purchase_event_for_order
from apps.core.logging import log_event
from apps.core.metrics import (
    inc_order_paid,
    inc_order_paid_duplicate,
    observe_payment_webhook_processing_duration,
)
from apps.custom_games.models import CustomGameRequest
from apps.custom_games.services import send_custom_game_download_link
from apps.notifications.services import enqueue_notification_outbox
from apps.orders.models import Order
from apps.orders.reward_services import issue_order_reward_after_payment
from libs.payments.models import InvoiceStatus

from .models import Invoice

logger = logging.getLogger("apps.payments")


def _send_custom_game_download_link_safely(
    custom_game_request: CustomGameRequest,
    invoice: Invoice,
    source: str,
) -> None:
    try:
        download_token = send_custom_game_download_link(custom_game_request=custom_game_request)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "custom_game_request.download_email.failed",
            exc_info=True,
            source=source,
            custom_game_request_id=custom_game_request.id,
            custom_game_request_public_id=str(custom_game_request.public_id),
            invoice_id=invoice.id,
            provider_invoice_no=invoice.provider_invoice_no,
        )
        return

    log_event(
        logger,
        logging.INFO,
        "custom_game_request.download_email.queued",
        source=source,
        custom_game_request_id=custom_game_request.id,
        custom_game_request_public_id=str(custom_game_request.public_id),
        invoice_id=invoice.id,
        provider_invoice_no=invoice.provider_invoice_no,
        download_token_id=download_token.id,
    )


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
            guest_access_email_outbox = create_guest_access_notification_for_order(order)
            if guest_access_email_outbox:
                guest_access_payloads = [{}] * order.items_count
                enqueue_notification_outbox(guest_access_email_outbox)
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
        transaction.on_commit(
            lambda: send_ga4_purchase_event_for_order(order_id=order.id, source=source),
        )
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

    issue_order_reward_after_payment(order.id)


def mark_custom_game_request_paid(
    custom_game_request: CustomGameRequest,
    invoice: Invoice,
    *,
    paid_at=None,
    source: str,
    persist: bool = True,
) -> None:
    paid_at = paid_at or timezone.now()
    already_delivered = custom_game_request.status == CustomGameRequest.Status.DELIVERED

    invoice.status = Invoice.InvoiceStatus.PAID
    invoice.paid_at = paid_at
    invoice.cancelled_at = None

    custom_game_request.status = CustomGameRequest.Status.DELIVERED
    custom_game_request.delivered_at = paid_at
    custom_game_request.cancelled_at = None

    if persist:
        invoice.save(
            update_fields=[
                "status",
                "paid_at",
                "cancelled_at",
                "updated_at",
            ],
        )
        custom_game_request.save(
            update_fields=[
                "status",
                "delivered_at",
                "cancelled_at",
                "updated_at",
            ],
        )

    if not already_delivered:
        transaction.on_commit(
            lambda: _send_custom_game_download_link_safely(custom_game_request, invoice, source),
        )

    log_event(
        logger,
        logging.INFO,
        "custom_game_request.paid.duplicate" if already_delivered else "custom_game_request.paid",
        source=source,
        custom_game_request_id=custom_game_request.id,
        custom_game_request_public_id=str(custom_game_request.public_id),
        invoice_id=invoice.id,
        provider_invoice_no=invoice.provider_invoice_no,
        user_id=custom_game_request.user_id,
    )


def apply_payable_status_from_invoice_status(
    invoice: Invoice,
    normalized_status: str | None,
    event_at,
    *,
    source: str,
) -> None:
    if invoice.order_id:
        apply_order_status_from_invoice_status(invoice, normalized_status, event_at, source=source)
        return

    if invoice.custom_game_request_id:
        apply_custom_game_request_status_from_invoice_status(invoice, normalized_status, event_at, source=source)


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


def apply_custom_game_request_status_from_invoice_status(
    invoice: Invoice,
    normalized_status: str | None,
    event_at,
    *,
    source: str,
) -> None:
    custom_game_request = invoice.custom_game_request
    if normalized_status == Invoice.InvoiceStatus.PAID:
        mark_custom_game_request_paid(
            custom_game_request,
            invoice,
            paid_at=event_at,
            source=source,
            persist=False,
        )
        return

    if normalized_status == Invoice.InvoiceStatus.CANCELED:
        invoice.paid_at = None
        invoice.cancelled_at = event_at or timezone.now()
        custom_game_request.status = CustomGameRequest.Status.CANCELLED
        custom_game_request.cancelled_at = invoice.cancelled_at
        return

    if normalized_status == Invoice.InvoiceStatus.EXPIRED:
        invoice.paid_at = None
        invoice.cancelled_at = None
        custom_game_request.status = CustomGameRequest.Status.PAYMENT_EXPIRED
        custom_game_request.cancelled_at = None
        return

    if normalized_status == Invoice.InvoiceStatus.PENDING:
        invoice.paid_at = None
        invoice.cancelled_at = None
        custom_game_request.status = CustomGameRequest.Status.WAITING_FOR_PAYMENT
        custom_game_request.cancelled_at = None


def save_invoice_and_target(invoice: Invoice) -> None:
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
    if invoice.order_id:
        invoice.order.save(
            update_fields=[
                "status",
                "paid_at",
                "cancelled_at",
                "failure_reason",
                "updated_at",
            ],
        )
    elif invoice.custom_game_request_id:
        invoice.custom_game_request.save(
            update_fields=[
                "status",
                "delivered_at",
                "cancelled_at",
                "updated_at",
            ],
        )


def apply_invoice_status_update(
    invoice: Invoice,
    *,
    provider_status: int | None,
    source: str,
    persist: bool = True,
    status_payload: dict | None = None,
) -> str | None:
    started_at = perf_counter()
    provider = invoice.provider or Invoice._meta.get_field("provider").default
    status_payload = status_payload or {}
    result = "success"
    try:
        normalized_status = map_invoice_status(provider_status)
        normalized_event_at = normalize_notification_datetime(status_payload.get("event_at"))

        invoice.provider = provider
        invoice.last_status_check_at = timezone.now()
        if "raw_response" in status_payload:
            invoice.raw_last_status_response = status_payload["raw_response"]
        if status_payload.get("amount") is not None:
            invoice.amount = status_payload["amount"]
        if status_payload.get("currency") is not None:
            invoice.currency = status_payload["currency"]
        if normalized_status is not None:
            invoice.status = normalized_status

        apply_payable_status_from_invoice_status(
            invoice,
            normalized_status,
            normalized_event_at,
            source=source,
        )

        if persist:
            save_invoice_and_target(invoice)

        return normalized_status
    except Exception:
        result = "error"
        raise
    finally:
        observe_payment_webhook_processing_duration(
            provider=provider,
            source=source,
            result=result,
            duration_seconds=perf_counter() - started_at,
        )
