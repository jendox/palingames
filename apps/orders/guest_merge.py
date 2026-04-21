from __future__ import annotations

import logging

from django.db import transaction

from apps.access.models import UserProductAccess
from apps.core.logging import log_event
from apps.core.metrics import inc_guest_orders_merged
from apps.orders.models import Order

logger = logging.getLogger("apps.orders.guest_merge")


def merge_guest_orders_for_user(*, user, email: str) -> int:
    normalized_email = (email or "").strip().lower()
    if not getattr(user, "pk", None) or not normalized_email:
        return 0

    with transaction.atomic():
        order_ids = list(
            Order.objects.select_for_update()
            .filter(user__isnull=True, email__iexact=normalized_email)
            .values_list("id", flat=True),
        )
        if not order_ids:
            return 0

        orders = list(
            Order.objects.filter(id__in=order_ids)
            .select_related("reward_promo_code")
            .prefetch_related("items__product"),
        )
        Order.objects.filter(id__in=order_ids, user__isnull=True).update(user=user)

        for order in orders:
            order.user = user
            if order.reward_promo_code_id and not order.reward_promo_code.assigned_user_id:
                order.reward_promo_code.assigned_user = user
                order.reward_promo_code.save(update_fields=["assigned_user"])

            if order.status != Order.OrderStatus.PAID:
                continue

            for item in order.items.all():
                UserProductAccess.objects.get_or_create(
                    user=user,
                    product=item.product,
                    defaults={"order": order},
                )

    log_event(
        logger,
        logging.INFO,
        "orders.guest_merge.completed",
        user_id=user.id,
        email=normalized_email,
        merged_orders_count=len(orders),
    )
    inc_guest_orders_merged(count=len(orders))
    return len(orders)
