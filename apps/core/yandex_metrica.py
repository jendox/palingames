from __future__ import annotations

import logging
import re

import httpx
from django.conf import settings
from django.utils import timezone

from apps.core.analytics_events import extract_file_extension
from apps.core.logging import log_event
from apps.orders.models import Order
from apps.products.pricing import get_currency_code

logger = logging.getLogger("apps.analytics")
YANDEX_MEASUREMENT_PROTOCOL_URL = "https://mc.yandex.ru/collect"
_YANDEX_CLIENT_ID_RE = re.compile(r"^\d{1,32}$")


def normalize_yandex_client_id(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if not _YANDEX_CLIENT_ID_RE.fullmatch(cleaned):
        return ""
    return cleaned


def read_yandex_client_id_from_request(request) -> str:
    return normalize_yandex_client_id(request.COOKIES.get("_ym_uid"))


def _yandex_server_events_enabled() -> bool:
    return (
        settings.ANALYTICS_ENABLED
        and settings.YANDEX_METRIKA_SERVER_EVENTS_ENABLED
        and bool(settings.YANDEX_METRIKA_ID)
        and bool(settings.YANDEX_METRIKA_MEASUREMENT_TOKEN)
    )


def _build_document_location(path: str) -> str:
    base = settings.SITE_BASE_URL.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base}{normalized_path}"


def _post_yandex_collect(*, payload: dict, log_context: dict, raise_on_failure: bool = False) -> bool:
    if not _yandex_server_events_enabled():
        log_event(
            logger,
            logging.INFO,
            "analytics.ym.skipped",
            reason="ym_not_configured",
            **log_context,
        )
        return False

    try:
        response = httpx.post(YANDEX_MEASUREMENT_PROTOCOL_URL, data=payload, timeout=5.0)
        response.raise_for_status()
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "analytics.ym.failed",
            exc_info=exc,
            error_type=type(exc).__name__,
            **log_context,
        )
        if raise_on_failure:
            raise
        return False

    log_event(
        logger,
        logging.INFO,
        "analytics.ym.sent",
        yandex_metrika_id=settings.YANDEX_METRIKA_ID,
        **log_context,
    )
    return True


def _post_yandex_pageview(
    *,
    client_id: str,
    document_location: str,
    document_title: str,
    log_context: dict,
    raise_on_failure: bool = False,
) -> bool:
    payload = {
        "tid": settings.YANDEX_METRIKA_ID,
        "cid": client_id,
        "t": "pageview",
        "ms": settings.YANDEX_METRIKA_MEASUREMENT_TOKEN,
        "dl": document_location,
        "dt": document_title,
    }
    return _post_yandex_collect(
        payload=payload,
        log_context={**log_context, "hit_type": "pageview"},
        raise_on_failure=raise_on_failure,
    )


def _post_yandex_event(  # noqa: PLR0913
    *,
    client_id: str,
    event_action: str,
    document_location: str,
    log_context: dict,
    event_value: float | None = None,
    currency: str | None = None,
    raise_on_failure: bool = False,
) -> bool:
    payload = {
        "tid": settings.YANDEX_METRIKA_ID,
        "cid": client_id,
        "t": "event",
        "ea": event_action,
        "ms": settings.YANDEX_METRIKA_MEASUREMENT_TOKEN,
        "dl": document_location,
    }
    if event_value is not None:
        payload["ev"] = str(event_value)
    if currency:
        payload["cu"] = currency

    return _post_yandex_collect(
        payload=payload,
        log_context={**log_context, "hit_type": "event", "event_action": event_action},
        raise_on_failure=raise_on_failure,
    )


def _yandex_purchase_skip_reason(order: Order | None, *, order_id: int) -> str | None:
    if order is None:
        return "order_not_found"
    if order.status != Order.OrderStatus.PAID:
        return "order_not_paid"
    if order.yandex_purchase_sent_at is not None:
        return "already_sent"
    if not order.analytics_storage_consent:
        return "no_analytics_consent"
    if not normalize_yandex_client_id(order.yandex_client_id):
        return "no_yandex_client_id"
    return None


def send_yandex_purchase_event_for_order(*, order_id: int, source: str) -> None:
    if not _yandex_server_events_enabled():
        log_event(
            logger,
            logging.INFO,
            "analytics.ym.purchase.skipped",
            order_id=order_id,
            source=source,
            reason="ym_not_configured",
        )
        return

    order = (
        Order.objects.select_related("user")
        .prefetch_related("items")
        .filter(pk=order_id)
        .first()
    )
    skip_reason = _yandex_purchase_skip_reason(order, order_id=order_id)
    if skip_reason:
        log_level = logging.WARNING if skip_reason == "order_not_found" else logging.INFO
        log_event(
            logger,
            log_level,
            "analytics.ym.purchase.skipped",
            order_id=order_id,
            order_public_id=str(order.public_id) if order is not None else None,
            source=source,
            reason=skip_reason,
        )
        return

    client_id = normalize_yandex_client_id(order.yandex_client_id)
    document_location = _build_document_location("/checkout/")
    document_title = "Оформление заказа — PalinGames"
    purchase_log_context = {
        "event_name": "purchase",
        "order_id": order_id,
        "order_public_id": str(order.public_id),
        "source": source,
    }

    _post_yandex_pageview(
        client_id=client_id,
        document_location=document_location,
        document_title=document_title,
        log_context=purchase_log_context,
        raise_on_failure=True,
    )
    sent = _post_yandex_event(
        client_id=client_id,
        event_action="purchase",
        document_location=document_location,
        event_value=float(order.total_amount),
        currency=get_currency_code(order.currency),
        log_context=purchase_log_context,
        raise_on_failure=True,
    )
    if sent:
        Order.objects.filter(pk=order_id, yandex_purchase_sent_at__isnull=True).update(
            yandex_purchase_sent_at=timezone.now(),
        )


def send_yandex_file_download_guest_event(
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
            "analytics.ym.file_download_guest.skipped",
            guest_access_id=guest_access.id,
            order_id=order.id,
            source=source,
            reason="no_analytics_consent",
        )
        return

    client_id = normalize_yandex_client_id(order.yandex_client_id)
    if not client_id:
        log_event(
            logger,
            logging.INFO,
            "analytics.ym.file_download_guest.skipped",
            guest_access_id=guest_access.id,
            order_id=order.id,
            source=source,
            reason="no_yandex_client_id",
        )
        return

    product = guest_access.product
    original_filename = product_file.original_filename or f"{product.slug}.zip"
    _post_yandex_event(
        client_id=client_id,
        event_action="file_download_guest",
        document_location=_build_document_location("/guest/download/"),
        log_context={
            "event_name": "file_download_guest",
            "guest_access_id": guest_access.id,
            "order_id": order.id,
            "product_id": product.id,
            "file_extension": extract_file_extension(original_filename),
            "source": source,
        },
    )


def send_yandex_file_download_custom_game_event(
    *,
    custom_game_request,
    custom_game_file,
    yandex_client_id: str,
    source: str,
) -> None:
    client_id = normalize_yandex_client_id(yandex_client_id)
    if not client_id:
        log_event(
            logger,
            logging.INFO,
            "analytics.ym.file_download_custom_game.skipped",
            custom_game_request_id=custom_game_request.id,
            source=source,
            reason="no_yandex_client_id",
        )
        return

    original_filename = (
        custom_game_file.original_filename or f"{custom_game_request.payment_account_no}.zip"
    )
    _post_yandex_event(
        client_id=client_id,
        event_action="file_download_custom_game",
        document_location=_build_document_location("/custom-game/download/"),
        log_context={
            "event_name": "file_download_custom_game",
            "custom_game_request_id": custom_game_request.id,
            "file_extension": extract_file_extension(original_filename),
            "source": source,
        },
    )
