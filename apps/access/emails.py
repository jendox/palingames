from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core.logging import log_event
from apps.emails.senders import OutboundEmail, send_outbound_email
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.products.pricing import format_price

logger = logging.getLogger("apps.access.email")


def build_absolute_url(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return urljoin(settings.SITE_BASE_URL.rstrip("/") + "/", path_or_url.lstrip("/"))


def send_guest_order_download_email(
    *,
    order: Order,
    guest_access_payloads: list[dict[str, Any]],
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    subject = f"Ссылки на скачивание заказа {order.payment_account_no}"
    items = [
        {
            "title": payload["title"],
            "category": payload["category"],
            "price": format_price(Decimal(payload["price"]), order.currency),
            "image_url": build_absolute_url(payload["image_url"]),
            "download_url": build_absolute_url(
                reverse("guest-product-download", kwargs={"token": payload["token"]}),
            ),
        }
        for payload in guest_access_payloads
    ]
    context = {
        "order": order,
        "items": items,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        "guest_access_expire_hours": settings.GUEST_ACCESS_EXPIRE_HOURS,
        "guest_access_max_downloads": settings.GUEST_ACCESS_MAX_DOWNLOADS,
    }
    text_body = render_to_string("access/email/guest_download_message.txt", context)
    html_body = render_to_string("access/email/guest_download_message.html", context)

    send_outbound_email(
        OutboundEmail(
            recipient=order.email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            notification_type=NotificationType.GUEST_ORDER_DOWNLOAD,
            template_key="access/email/guest_download_message",
            notification_outbox=notification_outbox,
            metadata={"order_id": order.id, "items_count": len(items)},
        ),
    )

    log_event(
        logger,
        logging.INFO,
        "guest_access.email.sent",
        order_id=order.id,
        order_public_id=str(order.public_id),
        items_count=len(items),
        email=order.email,
    )
