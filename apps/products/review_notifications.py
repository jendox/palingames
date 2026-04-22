from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from apps.core.logging import log_event
from apps.notifications.services import enqueue_notification
from apps.notifications.types import NotificationType
from apps.products.models import Review
from apps.products.telegram_notify import notify_review_submitted_telegram

logger = logging.getLogger("apps.products.review_notifications")


def _notify_admin_email(review: Review) -> None:
    recipients = [email for email in settings.REVIEW_ADMIN_EMAILS if email]
    if not recipients:
        log_event(
            logger,
            logging.WARNING,
            "review.notify.admin_email.enqueue_skipped",
            review_id=review.pk,
            reason="empty_recipients",
        )
        return

    try:
        enqueue_notification(
            notification_type=NotificationType.REVIEW_SUBMITTED_ADMIN,
            recipient=",".join(recipients),
            payload={"review_id": review.id},
            target=review,
        )
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "review.notify.admin_email.enqueue_failed",
            exc_info=True,
            review_id=review.id,
        )


def _notify_admin_telegram(review: Review) -> None:
    try:
        notify_review_submitted_telegram(review)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "review.notify.telegram.failed",
            exc_info=True,
            review_id=review.id,
        )


def schedule_review_submitted_notifications(review_id: int) -> None:
    def _send() -> None:
        review = Review.objects.select_related("product", "user").get(pk=review_id)
        _notify_admin_email(review)
        _notify_admin_telegram(review)

    transaction.on_commit(_send)
