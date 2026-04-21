from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event
from apps.orders.models import Order
from apps.promocodes.models import PromoCode

logger = logging.getLogger("apps.orders.email")


def send_order_reward_user_email(*, order: Order, promo_code: PromoCode) -> None:
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
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()
    log_event(
        logger,
        logging.INFO,
        "order.reward_user_email.sent",
        order_id=order.id,
        promo_code_id=promo_code.id,
    )
