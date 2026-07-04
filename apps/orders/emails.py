from __future__ import annotations

import logging

from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event
from apps.emails.senders import OutboundEmail, send_outbound_email
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.promocodes.models import PromoCode

logger = logging.getLogger("apps.orders.email")


def send_order_reward_user_email(
    *,
    order: Order,
    promo_code: PromoCode,
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    to_email = order.email
    if not to_email:
        log_event(
            logger,
            logging.WARNING,
            "order.reward_user_email.skipped",
            order_id=order.id,
            reason="empty_order_email",
        )
        return

    subject = f"Спасибо за заказ! Ваш промокод на {promo_code.discount_percent}%"
    context = {
        "order": order,
        "promo_code": promo_code,
        "discount_percent": promo_code.discount_percent,
        "expires_at": promo_code.ends_at,
        "catalog_url": build_absolute_url(reverse("catalog")),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
    }
    text_body = render_to_string("orders/email/order_reward_user.txt", context)
    html_body = render_to_string("orders/email/order_reward_user.html", context)

    send_outbound_email(
        OutboundEmail(
            recipient=order.email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            notification_type=NotificationType.ORDER_REWARD_USER,
            template_key="orders/email/order_reward_user",
            notification_outbox=notification_outbox,
            metadata={"order_id": order.id, "promo_code_id": promo_code.id},
        ),
    )

    log_event(
        logger,
        logging.INFO,
        "order.reward_user_email.sent",
        order_id=order.id,
        promo_code_id=promo_code.id,
    )
