from __future__ import annotations

import logging
import uuid

import httpx
from django.conf import settings

from apps.core.analytics_events import extract_file_extension
from apps.core.logging import log_event
from apps.orders.models import Order

logger = logging.getLogger("apps.analytics")
GA4_MEASUREMENT_PROTOCOL_URL = "https://www.google-analytics.com/mp/collect"


def _ga4_measurement_protocol_enabled() -> bool:
    return (
        settings.ANALYTICS_ENABLED
        and bool(settings.GA4_MEASUREMENT_ID)
        and bool(settings.GA4_API_SECRET)
    )


def _ga4_purchase_enabled() -> bool:
    return _ga4_measurement_protocol_enabled()


def _build_ga4_client_id_from_key(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"palingames-analytics:{key.strip().lower()}"))


def _build_ga4_client_id(order: Order) -> str:
    base = order.email.strip().lower() or str(order.public_id)
    return _build_ga4_client_id_from_key(f"order:{base}")


def _post_ga4_measurement_protocol(*, client_id: str, events: list[dict], log_context: dict) -> bool:
    if not _ga4_measurement_protocol_enabled():
        log_event(
            logger,
            logging.INFO,
            "analytics.mp.skipped",
            reason="ga4_not_configured",
            **log_context,
        )
        return False

    payload = {
        "client_id": client_id,
        "events": events,
    }
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
            "analytics.mp.failed",
            exc_info=exc,
            error_type=type(exc).__name__,
            **log_context,
        )
        return False

    log_event(
        logger,
        logging.INFO,
        "analytics.mp.sent",
        ga4_measurement_id=settings.GA4_MEASUREMENT_ID,
        **log_context,
    )
    return True


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
            order_id=order_id,
            order_public_id=str(order.public_id),
            source=source,
            reason="order_not_paid",
        )
        return

    if not order.analytics_storage_consent:
        log_event(
            logger,
            logging.INFO,
            "analytics.purchase.skipped",
            order_id=order_id,
            order_public_id=str(order.public_id),
            source=source,
            reason="no_analytics_consent",
        )
        return

    payload = _build_ga4_purchase_payload(order)
    _post_ga4_measurement_protocol(
        client_id=payload["client_id"],
        events=payload["events"],
        log_context={
            "event_name": "purchase",
            "order_id": order_id,
            "order_public_id": str(order.public_id),
            "source": source,
        },
    )


def _build_file_download_event_params(*, event_name: str, download_type: str, payload: dict) -> dict:
    params = {
        "file_name": payload["file_name"],
        "file_extension": payload["file_extension"],
        "item_id": payload["item_id"],
        "item_name": payload["item_name"],
        "download_type": download_type,
    }
    if payload.get("item_category"):
        params["item_category"] = payload["item_category"]
    if payload.get("item_variant"):
        params["item_variant"] = payload["item_variant"]
    return {
        "name": event_name,
        "params": params,
    }


def send_ga4_file_download_guest_event(
    *,
    guest_access,
    product_file,
    source: str,
) -> None:
    order = guest_access.order
    if not order.analytics_storage_consent:
        log_event(
            logger,
            logging.INFO,
            "analytics.file_download_guest.skipped",
            guest_access_id=guest_access.id,
            order_id=order.id,
            source=source,
            reason="no_analytics_consent",
        )
        return

    product = guest_access.product
    original_filename = product_file.original_filename or f"{product.slug}.zip"
    event = _build_file_download_event_params(
        event_name="file_download_guest",
        download_type="guest",
        payload={
            "file_name": product.title,
            "file_extension": extract_file_extension(original_filename),
            "item_id": str(product.id),
            "item_name": product.title,
        },
    )
    _post_ga4_measurement_protocol(
        client_id=_build_ga4_client_id(order),
        events=[event],
        log_context={
            "event_name": "file_download_guest",
            "guest_access_id": guest_access.id,
            "order_id": order.id,
            "product_id": product.id,
            "source": source,
        },
    )


def send_ga4_file_download_custom_game_event(
    *,
    custom_game_request,
    custom_game_file,
    source: str,
) -> None:
    original_filename = (
        custom_game_file.original_filename or f"{custom_game_request.payment_account_no}.zip"
    )
    event = _build_file_download_event_params(
        event_name="file_download_custom_game",
        download_type="custom_game",
        payload={
            "file_name": custom_game_request.subject,
            "file_extension": extract_file_extension(original_filename),
            "item_id": str(custom_game_request.public_id),
            "item_name": custom_game_request.subject,
        },
    )
    _post_ga4_measurement_protocol(
        client_id=_build_ga4_client_id_from_key(f"custom-game:{custom_game_request.public_id}"),
        events=[event],
        log_context={
            "event_name": "file_download_custom_game",
            "custom_game_request_id": custom_game_request.id,
            "source": source,
        },
    )
