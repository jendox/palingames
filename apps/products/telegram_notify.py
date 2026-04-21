from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from http import HTTPStatus

from django.conf import settings

from apps.core.logging import log_event
from apps.products.models import Review

logger = logging.getLogger("apps.products.telegram")


def notify_review_submitted_telegram(review: Review) -> None:
    token = getattr(settings, "REVIEW_TELEGRAM_BOT_TOKEN", "") or ""
    chat_id = getattr(settings, "REVIEW_TELEGRAM_CHAT_ID", "") or ""
    if not token or not chat_id:
        return

    product = review.product
    user = review.user
    text = (
        f"Новый отзыв #{review.id}\n"
        f"Товар: {product.title}\n"
        f"Пользователь: {user.email}\n"
        f"Оценка: {review.rating}/5"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status != HTTPStatus.OK:
                log_event(
                    logger,
                    logging.WARNING,
                    "review.telegram.unexpected_status",
                    review_id=review.id,
                    status=response.status,
                )
    except urllib.error.URLError as exc:
        log_event(
            logger,
            logging.WARNING,
            "review.telegram.failed",
            review_id=review.id,
            error_type=type(exc).__name__,
        )
