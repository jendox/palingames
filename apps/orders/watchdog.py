from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Literal

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.access.models import GuestAccess, UserProductAccess
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.payments.models import Invoice

WatchdogSeverity = Literal["critical", "warning"]

WATCHDOG_GRACE_PERIOD = timedelta(minutes=10)
WATCHDOG_LOOKBACK = timedelta(hours=48)


class OrderDeliveryProblemCode(StrEnum):
    INVOICE_MISSING = "invoice_missing"
    INVOICE_STATUS_MISMATCH = "invoice_status_mismatch"
    INVOICE_PAID_AT_MISSING = "invoice_paid_at_missing"
    AUTHENTICATED_ORDER_WITHOUT_USER = "authenticated_order_without_user"
    MISSING_USER_PRODUCT_ACCESS = "missing_user_product_access"
    MISSING_GUEST_ACCESS = "missing_guest_access"
    UNKNOWN_CHECKOUT_TYPE = "unknown_checkout_type"
    GUEST_DOWNLOAD_NOTIFICATION_MISSING = "guest_download_notification_missing"
    GUEST_DOWNLOAD_NOTIFICATION_FAILED = "guest_download_notification_failed"
    ORDER_PAID_AT_MISSING = "order_paid_at_missing"
    PAYMENT_INVOICE_MISSING = "payment_invoice_missing"


@dataclass(frozen=True)
class OrderDeliveryProblem:
    order_id: int
    code: OrderDeliveryProblemCode
    severity: WatchdogSeverity
    details: dict[str, str | int | None]


@dataclass(frozen=True)
class OrderDeliveryWatchdogResult:
    checked_orders: int
    checked_unpaid_orders: int
    problems: list[OrderDeliveryProblem]


def _check_invoice(order: Order) -> list[OrderDeliveryProblem]:
    problems: list[OrderDeliveryProblem] = []
    try:
        invoice = order.invoice
    except Invoice.DoesNotExist:
        return [
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.INVOICE_MISSING,
                severity="critical",
                details={},
            ),
        ]
    if invoice.status != Invoice.InvoiceStatus.PAID:
        problems.append(
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.INVOICE_STATUS_MISMATCH,
                severity="critical",
                details={
                    "invoice_id": invoice.id,
                    "invoice_status": invoice.status,
                    "order_status": order.status,
                },
            ),
        )
    if invoice.paid_at is None:
        problems.append(
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.INVOICE_PAID_AT_MISSING,
                severity="warning",
                details={
                    "invoice_id": invoice.id,
                },
            ),
        )

    return problems


def _get_order_product_ids(order: Order) -> set[int]:
    return {
        item.product_id
        for item in order.items.all()
    }


def _check_authenticated_accesses(order: Order) -> list[OrderDeliveryProblem]:
    if order.user_id is None:
        return [
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.AUTHENTICATED_ORDER_WITHOUT_USER,
                severity="critical",
                details={},
            ),
        ]
    expected_product_ids = _get_order_product_ids(order)
    existing_product_ids = set(
        UserProductAccess.objects.filter(
            user_id=order.user_id,
            product_id__in=expected_product_ids,
        ).values_list("product_id", flat=True),
    )
    missing = expected_product_ids - existing_product_ids

    return [
        OrderDeliveryProblem(
            order_id=order.id,
            code=OrderDeliveryProblemCode.MISSING_USER_PRODUCT_ACCESS,
            severity="critical",
            details={
                "product_id": product_id,
                "user_id": order.user_id,
            },
        )
        for product_id in sorted(missing)
    ]


def _check_guest_accesses(order: Order) -> list[OrderDeliveryProblem]:
    expected_product_ids = _get_order_product_ids(order)

    existing_product_ids = set(
        GuestAccess.objects.filter(
            order_id=order.id,
            product_id__in=expected_product_ids,
        ).values_list("product_id", flat=True),
    )

    missing = expected_product_ids - existing_product_ids

    return [
        OrderDeliveryProblem(
            order_id=order.id,
            code=OrderDeliveryProblemCode.MISSING_GUEST_ACCESS,
            severity="critical",
            details={
                "product_id": product_id,
            },
        )
        for product_id in sorted(missing)
    ]


def _check_accesses(order: Order) -> list[OrderDeliveryProblem]:
    if order.checkout_type == Order.CheckoutType.AUTHENTICATED:
        return _check_authenticated_accesses(order)
    if order.checkout_type == Order.CheckoutType.GUEST:
        return _check_guest_accesses(order)
    return [
        OrderDeliveryProblem(
            order_id=order.id,
            code=OrderDeliveryProblemCode.UNKNOWN_CHECKOUT_TYPE,
            severity="critical",
            details={
                "checkout_type": order.checkout_type,
            },
        ),
    ]


def _get_guest_download_outbox(order: Order) -> NotificationOutbox | None:
    order_content_type = ContentType.objects.get_for_model(Order, for_concrete_model=False)

    return (
        NotificationOutbox.objects
        .filter(
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            channel=NotificationOutbox.Channel.EMAIL,
            content_type=order_content_type,
            object_id=order.id,
        )
        .order_by("-created_at")
        .first()
    )


def _check_guest_notification(order: Order) -> list[OrderDeliveryProblem]:
    outbox = _get_guest_download_outbox(order)

    if outbox is None:
        return [
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.GUEST_DOWNLOAD_NOTIFICATION_MISSING,
                severity="critical",
                details={
                    "email": order.email,
                },
            ),
        ]

    if outbox.status == NotificationOutbox.Status.FAILED:
        return [
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.GUEST_DOWNLOAD_NOTIFICATION_FAILED,
                severity="critical",
                details={
                    "outbox_id": outbox.id,
                    "attempts": outbox.attempts,
                    "last_error": outbox.last_error,
                },
            ),
        ]

    return []


def check_paid_order_delivery(order: Order) -> list[OrderDeliveryProblem]:
    problems: list[OrderDeliveryProblem] = []

    if order.status != Order.OrderStatus.PAID:
        return problems
    if order.paid_at is None:
        problems.append(
            OrderDeliveryProblem(
                order_id=order.id,
                code=OrderDeliveryProblemCode.ORDER_PAID_AT_MISSING,
                severity="critical",
                details={},
            ),
        )

    problems.extend(_check_invoice(order))
    problems.extend(_check_accesses(order))

    if order.checkout_type == Order.CheckoutType.GUEST:
        problems.extend(_check_guest_notification(order))

    return problems


def _order_is_missing_payment_invoice(order: Order) -> bool:
    try:
        invoice = order.invoice
    except Invoice.DoesNotExist:
        return True

    provider_invoice_no = (invoice.provider_invoice_no or "").strip()
    invoice_url = (invoice.invoice_url or "").strip()
    return not provider_invoice_no or not invoice_url


def check_unpaid_order_missing_invoice(order: Order) -> list[OrderDeliveryProblem]:
    if order.status not in {
        Order.OrderStatus.CREATED,
        Order.OrderStatus.WAITING_FOR_PAYMENT,
    }:
        return []

    if not _order_is_missing_payment_invoice(order):
        return []

    details: dict[str, str | int | None] = {
        "order_public_id": str(order.public_id),
        "email": order.email,
        "order_status": order.status,
    }
    try:
        invoice = order.invoice
    except Invoice.DoesNotExist:
        details["invoice_id"] = None
    else:
        details["invoice_id"] = invoice.id
        details["provider_invoice_no"] = invoice.provider_invoice_no or ""
        details["invoice_url_present"] = bool((invoice.invoice_url or "").strip())

    return [
        OrderDeliveryProblem(
            order_id=order.id,
            code=OrderDeliveryProblemCode.PAYMENT_INVOICE_MISSING,
            severity="critical",
            details=details,
        ),
    ]


def get_paid_orders_for_watchdog():
    now = timezone.now()

    return (
        Order.objects.filter(
            status=Order.OrderStatus.PAID,
            paid_at__isnull=False,
            paid_at__lte=now - WATCHDOG_GRACE_PERIOD,
            paid_at__gte=now - WATCHDOG_LOOKBACK,
        )
        .select_related("user", "invoice")
        .prefetch_related("items")
    )


def get_paid_orders_with_missing_paid_at_for_watchdog():
    now = timezone.now()

    return (
        Order.objects.filter(
            status=Order.OrderStatus.PAID,
            paid_at__isnull=True,
            created_at__gte=now - WATCHDOG_LOOKBACK,
        )
        .select_related("user", "invoice")
        .prefetch_related("items")
    )


def get_unpaid_orders_missing_invoice_for_watchdog():
    now = timezone.now()

    return (
        Order.objects.filter(
            status__in=[
                Order.OrderStatus.CREATED,
                Order.OrderStatus.WAITING_FOR_PAYMENT,
            ],
            created_at__lte=now - WATCHDOG_GRACE_PERIOD,
            created_at__gte=now - WATCHDOG_LOOKBACK,
        )
        .select_related("invoice")
        .order_by("created_at", "id")
    )


def run_order_delivery_watchdog() -> OrderDeliveryWatchdogResult:
    checked = 0
    checked_unpaid = 0
    problems: list[OrderDeliveryProblem] = []

    for order in get_unpaid_orders_missing_invoice_for_watchdog():
        checked_unpaid += 1
        problems.extend(check_unpaid_order_missing_invoice(order))

    for order in get_paid_orders_with_missing_paid_at_for_watchdog():
        checked += 1
        problems.extend(check_paid_order_delivery(order))

    for order in get_paid_orders_for_watchdog():
        checked += 1
        problems.extend(check_paid_order_delivery(order))

    return OrderDeliveryWatchdogResult(
        checked_orders=checked,
        checked_unpaid_orders=checked_unpaid,
        problems=problems,
    )
