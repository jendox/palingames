from __future__ import annotations

from django.db import models

from apps.payments.models import Invoice

from .models import Order, OrderItem


def get_user_pending_order_item_by_product_ids(user, *, product_ids: list[int]):
    if not getattr(user, "is_authenticated", False) or not product_ids:
        return None

    return (
        OrderItem.objects.select_related("order")
        .filter(
            order__user=user,
            product_id__in=product_ids,
            order__status__in=[Order.OrderStatus.CREATED, Order.OrderStatus.WAITING_FOR_PAYMENT],
        )
        .filter(
            models.Q(order__status=Order.OrderStatus.CREATED)
            | models.Q(order__invoice__status=Invoice.InvoiceStatus.PENDING)
            | models.Q(order__invoice__isnull=True),
        )
        .order_by("-order__created_at", "-order_id", "id")
        .first()
    )


def build_pending_purchase_message(*, order: Order, product_title: str) -> str:
    order_number = order.payment_account_no or f"#{order.id}"
    return (
        f"У вас уже есть неоплаченный заказ {order_number} с товаром «{product_title}». "
        "Завершите оплату текущего заказа или дождитесь истечения срока оплаты."
    )
