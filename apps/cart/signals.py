from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .services import merge_guest_cart_to_user


@receiver(user_logged_in)
def merge_guest_cart_after_login(sender, user, request, **kwargs):
    if request is None:
        return
    merge_guest_cart_to_user(request, user)

