import logging

from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from apps.core.logging import log_event
from apps.orders.guest_merge import merge_guest_orders_for_user

logger = logging.getLogger("apps.users.signals")


@receiver(email_confirmed)
def merge_guest_orders_after_email_confirmation(request, email_address, **kwargs):
    merged_orders_count = merge_guest_orders_for_user(
        user=email_address.user,
        email=email_address.email,
    )
    log_event(
        logger,
        logging.INFO,
        "users.email_confirmed.guest_orders_merged",
        user_id=email_address.user_id,
        email=email_address.email,
        merged_orders_count=merged_orders_count,
    )
