from __future__ import annotations

import logging
import secrets
import string
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.logging import log_event
from apps.core.metrics import inc_order_reward_issued, inc_order_reward_skipped
from apps.notifications.models import NotificationOutbox
from apps.notifications.services import enqueue_email_notification
from apps.notifications.types import NotificationType
from apps.orders.models import Order
from apps.promocodes.models import PromoCode

logger = logging.getLogger("apps.orders.rewards")

PROMO_CODE_ALPHABET = string.ascii_uppercase + string.digits
PROMO_CODE_SUFFIX_LEN = 8
PROMO_CODE_MAX_ATTEMPTS = 10


def ensure_order_reward(order: Order) -> PromoCode | None:
    skip_reason = _get_order_reward_skip_reason(order)
    if skip_reason is not None:
        inc_order_reward_skipped(reason=skip_reason)
        return None

    if order.reward_promo_code_id:
        return order.reward_promo_code

    promo_code = _create_order_reward_promo_code(order)
    now = timezone.now()
    Order.objects.filter(pk=order.pk, reward_promo_code__isnull=True).update(
        reward_promo_code=promo_code,
        reward_issued_at=now,
    )
    order.refresh_from_db(fields=["reward_promo_code", "reward_issued_at"])
    log_event(
        logger,
        logging.INFO,
        "order.reward.issued",
        order_id=order.id,
        promo_code_id=promo_code.id,
        user_id=order.user_id,
        email=order.email,
    )
    inc_order_reward_issued()
    return order.reward_promo_code


def _has_order_reward_email_notification(order: Order) -> bool:
    content_type = ContentType.objects.get_for_model(order, for_concrete_model=False)
    return NotificationOutbox.objects.filter(
        notification_type=NotificationType.ORDER_REWARD_USER,
        content_type=content_type,
        object_id=order.pk,
        status__in=[
            NotificationOutbox.Status.PENDING,
            NotificationOutbox.Status.PROCESSING,
            NotificationOutbox.Status.SENT,
        ],
    ).exists()


def ensure_order_reward_email(order: Order) -> None:
    if _get_order_reward_skip_reason(order) is not None:
        return

    promo_code = ensure_order_reward(order)
    if promo_code is None or order.reward_email_sent_at:
        return

    if _has_order_reward_email_notification(order):
        return

    try:
        enqueue_email_notification(
            notification_type=NotificationType.ORDER_REWARD_USER,
            recipient=order.email,
            payload={
                "order_id": order.id,
                "promo_code_id": promo_code.id,
            },
            target=order,
        )
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "order.reward.email.enqueue_failed",
            exc_info=True,
            order_id=order.id,
            promo_code_id=promo_code.id,
        )


def issue_order_reward_after_payment(order_id: int) -> None:
    def _run() -> None:
        order = Order.objects.select_related("user", "promo_code", "reward_promo_code").get(pk=order_id)
        ensure_order_reward_email(order)

    transaction.on_commit(_run)


def _get_order_reward_skip_reason(order: Order) -> str | None:
    if order.status != Order.OrderStatus.PAID:
        return "status_not_paid"
    if order.total_amount < Decimal(settings.ORDER_REWARD_MIN_TOTAL_AMOUNT):
        return "below_threshold"
    if order.promo_code_id and order.promo_code and order.promo_code.is_reward:
        return "reward_promo_used"
    return None


def _create_order_reward_promo_code(order: Order) -> PromoCode:
    for _ in range(PROMO_CODE_MAX_ATTEMPTS):
        code = _generate_reward_code(order.id)
        try:
            return PromoCode.objects.create(
                code=code,
                discount_percent=settings.ORDER_REWARD_DISCOUNT_PERCENT,
                is_reward=True,
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=settings.ORDER_REWARD_VALID_DAYS),
                max_total_redemptions=1,
                max_redemptions_per_user=1 if order.user_id else None,
                max_redemptions_per_email=1,
                assigned_user=order.user if order.user_id else None,
                assigned_email=(order.email or "").strip().lower(),
                note=f"Reward for paid order #{order.id}",
            )
        except IntegrityError:
            continue
    raise RuntimeError(f"Could not generate unique promo code for order #{order.id}")


def _generate_reward_code(order_id: int) -> str:
    suffix = "".join(secrets.choice(PROMO_CODE_ALPHABET) for _ in range(PROMO_CODE_SUFFIX_LEN))
    return f"ORDER{order_id}-{suffix}"
