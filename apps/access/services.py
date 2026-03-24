from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from apps.orders.models import Order

from .models import GuestAccess, UserProductAccess

GUEST_ACCESS_TOKEN_BYTES = 32


def hash_guest_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_product_access_ids(user, *, product_ids=None) -> set[int]:
    if not getattr(user, "is_authenticated", False):
        return set()

    queryset = UserProductAccess.objects.filter(user=user)
    if product_ids is not None:
        queryset = queryset.filter(product_id__in=product_ids)
    return set(queryset.values_list("product_id", flat=True))


def has_user_product_access(user, product_id: int) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return UserProductAccess.objects.filter(user=user, product_id=product_id).exists()


def grant_user_product_accesses(order: Order) -> None:
    if not order.user_id:
        return

    for item in order.items.select_related("product").all():
        UserProductAccess.objects.get_or_create(
            user=order.user,
            product=item.product,
            defaults={"order": order},
        )


def create_guest_access(*, order: Order, product, expires_in: timedelta) -> tuple[GuestAccess, str]:
    raw_token = secrets.token_urlsafe(GUEST_ACCESS_TOKEN_BYTES)
    token_hash = hash_guest_access_token(raw_token)
    guest_access, _ = GuestAccess.objects.update_or_create(
        order=order,
        product=product,
        defaults={
            "token_hash": token_hash,
            "expires_at": timezone.now() + expires_in,
            "used_at": None,
            "revoked_at": None,
            "email": order.email,
        },
    )
    return guest_access, raw_token


def resolve_guest_access(raw_token: str) -> GuestAccess | None:
    token_hash = hash_guest_access_token(raw_token)
    guest_access = (
        GuestAccess.objects.select_related("order", "product")
        .filter(token_hash=token_hash)
        .first()
    )
    if guest_access is None or not guest_access.is_active:
        return None
    return guest_access


def mark_guest_access_used(guest_access: GuestAccess) -> None:
    if guest_access.used_at is not None:
        return
    guest_access.used_at = timezone.now()
    guest_access.save(update_fields=["used_at", "updated_at"])
