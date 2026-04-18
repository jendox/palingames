from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.core.logging import log_event
from apps.orders.models import Order

from .email_outbox import create_guest_access_email_outbox
from .models import GuestAccess, UserProductAccess

GUEST_ACCESS_TOKEN_BYTES = 32
logger = logging.getLogger("apps.access")


def hash_guest_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _guest_access_expiration_delta() -> timedelta:
    return timedelta(hours=settings.GUEST_ACCESS_EXPIRE_HOURS)


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


def grant_guest_product_accesses(order: Order) -> list[dict]:
    if order.user_id or order.checkout_type != Order.CheckoutType.GUEST:
        return []

    guest_access_payloads: list[dict] = []
    for item in order.items.select_related("product").all():
        guest_access, raw_token = create_guest_access(
            order=order,
            product=item.product,
            expires_in=_guest_access_expiration_delta(),
            max_downloads=settings.GUEST_ACCESS_MAX_DOWNLOADS,
        )
        guest_access_payloads.append(
            {
                "guest_access_id": guest_access.id,
                "product_id": item.product_id,
                "title": item.title_snapshot,
                "category": item.category_snapshot,
                "price": str(item.line_total_amount),
                "image_url": item.product_image_snapshot or "",
                "token": raw_token,
            },
        )

    log_event(
        logger,
        logging.INFO,
        "guest_access.granted",
        order_id=order.id,
        order_public_id=str(order.public_id),
        items_count=len(guest_access_payloads),
        email=order.email,
        max_downloads=settings.GUEST_ACCESS_MAX_DOWNLOADS,
        expires_in_hours=settings.GUEST_ACCESS_EXPIRE_HOURS,
    )
    return guest_access_payloads


def create_guest_access_email_outbox_for_order(order: Order):
    guest_access_payloads = grant_guest_product_accesses(order)
    if not guest_access_payloads:
        return None
    return create_guest_access_email_outbox(
        order=order,
        guest_access_payloads=guest_access_payloads,
    )


def create_guest_access(
    *,
    order: Order,
    product,
    expires_in: timedelta,
    max_downloads: int = 3,
) -> tuple[GuestAccess, str]:
    raw_token = secrets.token_urlsafe(GUEST_ACCESS_TOKEN_BYTES)
    token_hash = hash_guest_access_token(raw_token)
    guest_access, _ = GuestAccess.objects.update_or_create(
        order=order,
        product=product,
        defaults={
            "token_hash": token_hash,
            "expires_at": timezone.now() + expires_in,
            "last_used_at": None,
            "downloads_count": 0,
            "max_downloads": max_downloads,
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


def mark_guest_access_used(guest_access: GuestAccess) -> bool:
    updated = GuestAccess.objects.filter(
        pk=guest_access.pk,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
        downloads_count__lt=F("max_downloads"),
    ).update(
        downloads_count=F("downloads_count") + 1,
        last_used_at=timezone.now(),
        updated_at=timezone.now(),
    )
    if not updated:
        return False
    guest_access.refresh_from_db(fields=["downloads_count", "last_used_at", "updated_at"])
    return True


def release_guest_access_use(guest_access: GuestAccess) -> None:
    updated = GuestAccess.objects.filter(
        pk=guest_access.pk,
        downloads_count__gt=0,
    ).update(
        downloads_count=F("downloads_count") - 1,
        updated_at=timezone.now(),
    )
    if updated:
        guest_access.refresh_from_db(fields=["downloads_count", "updated_at"])
