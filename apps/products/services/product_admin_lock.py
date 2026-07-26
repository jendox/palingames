from __future__ import annotations

from django.conf import settings
from django.core.cache import caches


def _product_admin_save_lock_key(product_id: int) -> str:
    return f"product_admin_save_lock:{product_id}"


def try_acquire_product_admin_save_lock(product_id: int) -> bool:
    return caches["default"].add(
        _product_admin_save_lock_key(product_id),
        True,
        timeout=settings.PRODUCT_ADMIN_SAVE_LOCK_SECONDS,
    )


def release_product_admin_save_lock(product_id: int) -> None:
    caches["default"].delete(_product_admin_save_lock_key(product_id))
