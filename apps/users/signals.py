import logging

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.core.logging import log_event
from apps.orders.guest_merge import merge_guest_orders_for_user

logger = logging.getLogger("apps.users.signals")


def _merge_guest_orders_for_verified_email(*, user, email: str, event: str) -> None:
    merged_orders_count = merge_guest_orders_for_user(
        user=user,
        email=email,
    )
    log_event(
        logger,
        logging.INFO,
        event,
        user_id=user.id,
        email=email,
        merged_orders_count=merged_orders_count,
    )


@receiver(email_confirmed)
def merge_guest_orders_after_email_confirmation(request, email_address, **kwargs):
    _merge_guest_orders_for_verified_email(
        user=email_address.user,
        email=email_address.email,
        event="users.email_confirmed.guest_orders_merged",
    )


@receiver(user_logged_in)
def merge_guest_orders_after_login(sender, request, user, **kwargs):
    verified_email = (
        EmailAddress.objects.filter(user=user, verified=True)
        .order_by("-primary", "-id")
        .values_list("email", flat=True)
        .first()
    )
    if not verified_email:
        return

    _merge_guest_orders_for_verified_email(
        user=user,
        email=verified_email,
        event="users.login.guest_orders_merged",
    )
