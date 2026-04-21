from __future__ import annotations

import logging
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.logging import log_event
from apps.core.metrics import inc_review_reward_issued
from apps.products.emails import send_review_reward_user_email
from apps.products.models import Review, ReviewStatus
from apps.promocodes.models import PromoCode

logger = logging.getLogger("apps.products.review_rewards")

PROMO_CODE_ALPHABET = string.ascii_uppercase + string.digits
PROMO_CODE_SUFFIX_LEN = 8
PROMO_CODE_MAX_ATTEMPTS = 10


def ensure_review_reward(review: Review) -> PromoCode | None:
    if review.status != ReviewStatus.PUBLISHED:
        return None

    if review.reward_promo_code_id:
        return review.reward_promo_code

    promo_code = _create_review_reward_promo_code(review)
    now = timezone.now()
    Review.objects.filter(pk=review.pk, reward_promo_code__isnull=True).update(
        reward_promo_code=promo_code,
        reward_issued_at=now,
    )
    review.refresh_from_db(fields=["reward_promo_code", "reward_issued_at"])
    log_event(
        logger,
        logging.INFO,
        "review.reward.issued",
        review_id=review.id,
        promo_code_id=promo_code.id,
        user_id=review.user_id,
    )
    inc_review_reward_issued()
    return review.reward_promo_code


def ensure_review_reward_email(review: Review) -> None:
    if review.status != ReviewStatus.PUBLISHED:
        return

    promo_code = ensure_review_reward(review)
    if promo_code is None or review.reward_email_sent_at:
        return

    try:
        send_review_reward_user_email(review=review, promo_code=promo_code)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "review.reward.email.failed",
            exc_info=True,
            review_id=review.id,
            promo_code_id=promo_code.id,
        )
        return

    now = timezone.now()
    Review.objects.filter(pk=review.pk, reward_email_sent_at__isnull=True).update(
        reward_email_sent_at=now,
    )
    review.reward_email_sent_at = now


def issue_review_reward_after_publish(review_id: int) -> None:
    def _run() -> None:
        review = Review.objects.select_related("product", "user", "reward_promo_code").get(pk=review_id)
        ensure_review_reward_email(review)

    transaction.on_commit(_run)


def _create_review_reward_promo_code(review: Review) -> PromoCode:
    for _ in range(PROMO_CODE_MAX_ATTEMPTS):
        code = _generate_reward_code(review.id)
        try:
            return PromoCode.objects.create(
                code=code,
                discount_percent=settings.REVIEW_REWARD_DISCOUNT_PERCENT,
                is_reward=True,
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=settings.REVIEW_REWARD_VALID_DAYS),
                max_total_redemptions=1,
                max_redemptions_per_user=1,
                max_redemptions_per_email=1,
                assigned_user=review.user,
                assigned_email=(review.user.email or "").strip().lower(),
                note=f"Reward for published review #{review.id}",
            )
        except IntegrityError:
            continue
    raise RuntimeError(f"Could not generate unique promo code for review #{review.id}")


def _generate_reward_code(review_id: int) -> str:
    suffix = "".join(secrets.choice(PROMO_CODE_ALPHABET) for _ in range(PROMO_CODE_SUFFIX_LEN))
    return f"REVIEW{review_id}-{suffix}"
