from __future__ import annotations

import logging

from django.db import transaction

from apps.core.logging import log_event
from apps.products.emails import send_review_submitted_admin_email
from apps.products.telegram_notify import notify_review_submitted_telegram

logger = logging.getLogger("apps.products.review_notifications")


def schedule_review_submitted_notifications(review_id: int) -> None:
    def _send() -> None:
        from apps.products.models import Review

        review = Review.objects.select_related("product", "user").get(pk=review_id)
        try:
            send_review_submitted_admin_email(review=review)
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "review.notify.admin_email.failed",
                exc_info=True,
                review_id=review_id,
            )
        try:
            notify_review_submitted_telegram(review)
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "review.notify.telegram.failed",
                exc_info=True,
                review_id=review_id,
            )

    transaction.on_commit(_send)
