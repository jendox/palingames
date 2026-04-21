from __future__ import annotations

import logging
import uuid

import httpx
from django.conf import settings

from apps.core.logging import log_event
from apps.orders.models import Order

logger = logging.getLogger("apps.analytics")
GA4_MEASUREMENT_PROTOCOL_URL = "https://www.google-analytics.com/mp/collect"


def _ga4_purchase_enabled() -> bool:
    return (
        settings.ANALYTICS_ENABLED
        and bool(settings.GA4_MEASUREMENT_ID)
        and bool(settings.GA4_API_SECRET)
    )


def _build_ga4_client_id(order: Order) -> str:
    base = order.email.strip().lower() or str(order.public_id)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"palingames-order:{base}"))


def _build_ga4_purchase_payload(order: Order) -> dict:
    items = []
    for item in order.items.order_by("id"):
        effective_line_total = item.discounted_line_total_amount or item.line_total_amount
        quantity = item.quantity or 1
        items.append(
            {
                "item_id": (
                    str(item.product_id)
                    if item.product_id
                    else item.product_slug_snapshot or item.title_snapshot
                ),
                "item_name": item.title_snapshot,
                "item_category": item.category_snapshot,
                "price": float(effective_line_total / quantity),
                "quantity": quantity,
            },
        )

    event_params = {
        "transaction_id": str(order.public_id),
        "currency": order.currency,
        "value": float(order.total_amount),
        "coupon": order.promo_code_snapshot or None,
        "items": items,
    }

    payload = {
        "client_id": _build_ga4_client_id(order),
        "events": [
            {
                "name": "purchase",
                "params": event_params,
            },
        ],
    }
    if order.user_id:
        payload["user_id"] = str(order.user_id)
    return payload


def send_ga4_purchase_event_for_order(*, order_id: int, source: str) -> None:
    if not _ga4_purchase_enabled():
        log_event(
            logger,
            logging.INFO,
            "analytics.purchase.skipped",
            order_id=order_id,
            source=source,
            reason="ga4_not_configured",
        )
        return

    order = (
        Order.objects.select_related("user")
        .prefetch_related("items")
        .filter(pk=order_id)
        .first()
    )
    if order is None:
        log_event(
            logger,
            logging.WARNING,
            "analytics.purchase.skipped",
            order_id=order_id,
            source=source,
            reason="order_not_found",
        )
        return

    if order.status != Order.OrderStatus.PAID:
        log_event(
            logger,
            logging.INFO,
            "analytics.purchase.skipped",
            order_id=order.id,
            order_public_id=str(order.public_id),
            source=source,
            reason="order_not_paid",
        )
        return

    payload = _build_ga4_purchase_payload(order)
    try:
        response = httpx.post(
            GA4_MEASUREMENT_PROTOCOL_URL,
            params={
                "measurement_id": settings.GA4_MEASUREMENT_ID,
                "api_secret": settings.GA4_API_SECRET,
            },
            json=payload,
            timeout=5.0,
        )
        response.raise_for_status()
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "analytics.purchase.failed",
            exc_info=exc,
            order_id=order.id,
            order_public_id=str(order.public_id),
            source=source,
            error_type=type(exc).__name__,
        )
        return

    log_event(
        logger,
        logging.INFO,
        "analytics.purchase.sent",
        order_id=order.id,
        order_public_id=str(order.public_id),
        source=source,
        ga4_measurement_id=settings.GA4_MEASUREMENT_ID,
    )
