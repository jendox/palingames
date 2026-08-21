from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
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


@dataclass(frozen=True)
class OrderDeliveryProblem:
    order_id: int
    code: str
    severity: WatchdogSeverity
    details: dict[str, str | int | None]


@dataclass(frozen=True)
class OrderDeliveryWatchdogResult:
    checked_orders: int
    problems: list[OrderDeliveryProblem]


def _check_invoice(order: Order) -> list[OrderDeliveryProblem]:
    problems: list[OrderDeliveryProblem] = []
    try:
        invoice = order.invoice
    except Invoice.DoesNotExist:
        return [
            OrderDeliveryProblem(
                order_id=order.id,
                code="invoice_missing",
                severity="critical",
                details={},
            ),
        ]
    if invoice.status != Invoice.InvoiceStatus.PAID:
        problems.append(
            OrderDeliveryProblem(
                order_id=order.id,
                code="invoice_status_mismatch",
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
                code="invoice_paid_at_missing",
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
                code="authenticated_order_without_user",
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
            code="missing_user_product_access",
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
            code="missing_guest_access",
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
            code="unknown_checkout_type",
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
                code="guest_download_notification_missing",
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
                code="guest_download_notification_failed",
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
                code="order_paid_at_missing",
                severity="critical",
                details={},
            ),
        )

    problems.extend(_check_invoice(order))
    problems.extend(_check_accesses(order))

    if order.checkout_type == Order.CheckoutType.GUEST:
        problems.extend(_check_guest_notification(order))

    return problems


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


def run_order_delivery_watchdog() -> OrderDeliveryWatchdogResult:
    checked = 0
    problems: list[OrderDeliveryProblem] = []

    for order in get_paid_orders_with_missing_paid_at_for_watchdog():
        checked += 1
        problems.extend(check_paid_order_delivery(order))

    for order in get_paid_orders_for_watchdog():
        checked += 1
        problems.extend(check_paid_order_delivery(order))

    return OrderDeliveryWatchdogResult(
        checked_orders=checked,
        problems=problems,
    )
