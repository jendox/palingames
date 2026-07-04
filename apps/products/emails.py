from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event
from apps.emails.senders import OutboundEmail, send_outbound_email
from apps.notifications.models import NotificationOutbox
from apps.notifications.types import NotificationType
from apps.products.models import Review
from apps.promocodes.models import PromoCode

logger = logging.getLogger("apps.products.email")


def _admin_recipients() -> list[str]:
    return [email for email in settings.REVIEW_ADMIN_EMAILS if email]


def send_review_submitted_admin_email(
    *,
    review: Review,
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    recipients = _admin_recipients()
    if not recipients:
        log_event(
            logger,
            logging.INFO,
            "review.admin_email.skipped",
            review_id=review.id,
            reason="empty_recipients",
        )
        return

    product = review.product
    subject = f"Новый отзыв на «{product.title}» (модерация)"
    context = {
        "review": review,
        "product": product,
        "user": review.user,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        "admin_review_url": build_absolute_url(
            reverse("admin:products_review_change", args=[review.id]),
        ),
    }
    text_body = render_to_string("products/email/review_submitted_admin.txt", context)
    html_body = render_to_string("products/email/review_submitted_admin.html", context)

    for recipient in recipients:
        send_outbound_email(
            OutboundEmail(
                recipient=recipient,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
                template_key="products/email/review_submitted_admin",
                notification_outbox=notification_outbox,
                metadata={"user_id": review.user.id, "product_id": product.id, "review_id": review.id},
            ),
        )

    log_event(
        logger,
        logging.INFO,
        "review.admin_email.sent",
        review_id=review.id,
        product_id=product.id,
        recipients_count=len(recipients),
    )


def send_review_rejected_user_email(
    *,
    review: Review,
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    to_email = review.user.email
    if not to_email:
        log_event(
            logger,
            logging.WARNING,
            "review.rejection_user_email.skipped",
            review_id=review.id,
            reason="empty_user_email",
        )
        return

    product = review.product
    subject = f"Отзыв на «{product.title}» не прошёл модерацию"
    context = {
        "review": review,
        "product": product,
        "user": review.user,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "product_url": build_absolute_url(reverse("product-detail", kwargs={"slug": product.slug})),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
    }
    text_body = render_to_string("products/email/review_rejected_user.txt", context)
    html_body = render_to_string("products/email/review_rejected_user.html", context)

    send_outbound_email(
        OutboundEmail(
            recipient=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            notification_type=NotificationType.REVIEW_REJECTED_USER,
            template_key="products/email/review_rejected_user",
            notification_outbox=notification_outbox,
            metadata={"user_id": review.user.id, "product_id": product.id, "review_id": review.id},
        ),
    )

    log_event(
        logger,
        logging.INFO,
        "review.rejection_user_email.sent",
        review_id=review.id,
        product_id=product.id,
    )


def send_review_reward_user_email(
    *,
    review: Review,
    promo_code: PromoCode,
    notification_outbox: NotificationOutbox | None = None,
) -> None:
    to_email = review.user.email
    if not to_email:
        log_event(
            logger,
            logging.WARNING,
            "review.reward_user_email.skipped",
            review_id=review.id,
            reason="empty_user_email",
        )
        return

    product = review.product
    subject = f"Спасибо за отзыв! Ваш промокод на 10% для «{product.title}»"
    context = {
        "review": review,
        "product": product,
        "user": review.user,
        "promo_code": promo_code,
        "discount_percent": promo_code.discount_percent,
        "expires_at": promo_code.ends_at,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "catalog_url": build_absolute_url(reverse("catalog")),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
    }
    text_body = render_to_string("products/email/review_reward_user.txt", context)
    html_body = render_to_string("products/email/review_reward_user.html", context)

    send_outbound_email(
        OutboundEmail(
            recipient=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            notification_type=NotificationType.REVIEW_REWARD_USER,
            template_key="products/email/review_reward_user",
            notification_outbox=notification_outbox,
            metadata={"user_id": review.user.id, "product_id": product.id, "review_id": review.id},
        ),
    )

    log_event(
        logger,
        logging.INFO,
        "review.reward_user_email.sent",
        review_id=review.id,
        product_id=product.id,
        promo_code_id=promo_code.id,
    )
