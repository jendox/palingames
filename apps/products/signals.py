from __future__ import annotations

import logging
import time

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.core.logging import get_request_id, log_event
from apps.custom_games.models import CustomGameFile
from apps.products.models import ProductFile, ProductImage
from apps.products.services.product_image_cleanup import (
    DELETE_REASON_REPLACE,
    DELETE_REASON_ROW_DELETE,
    schedule_product_image_delete_on_commit,
)
from apps.products.services.s3 import (
    ProductFileDeleteError,
    delete_product_file,
    is_deletable_download_file_key,
)

logger = logging.getLogger("apps.products.storage")


def _product_image_will_change(instance: ProductImage, old_key: str | None) -> bool:
    if not instance.image:
        return bool(old_key)
    if not instance.pk:
        return True
    if not old_key:
        return True
    field_file = instance.image
    if not getattr(field_file, "_committed", True):
        return True
    return field_file.name != old_key


@receiver(pre_save, sender=ProductImage)
def capture_product_image_replace_context(sender, instance: ProductImage, **kwargs) -> None:
    instance._product_image_old_key = None
    instance._product_image_replace_started_at = None

    if not instance.pk:
        if instance.image:
            log_event(
                logger,
                logging.INFO,
                "product_image.replace.started",
                product_id=instance.product_id,
                reason="create",
                request_id=get_request_id(),
            )
        return

    try:
        old = ProductImage.objects.only("image", "product_id").get(pk=instance.pk)
    except ProductImage.DoesNotExist:
        return

    old_key = old.image.name or None
    instance._product_image_old_key = old_key
    if not _product_image_will_change(instance, old_key):
        return

    instance._product_image_replace_started_at = time.monotonic()
    log_event(
        logger,
        logging.INFO,
        "product_image.replace.started",
        product_id=instance.product_id,
        product_image_id=instance.pk,
        old_object_key=old_key,
        request_id=get_request_id(),
    )


@receiver(post_save, sender=ProductImage)
def schedule_replaced_product_image_delete(sender, instance: ProductImage, created: bool, **kwargs) -> None:
    old_key = getattr(instance, "_product_image_old_key", None)
    new_key = instance.image.name if instance.image else ""
    replace_started_at = getattr(instance, "_product_image_replace_started_at", None)

    if old_key and old_key != new_key:
        duration_ms = None
        if replace_started_at is not None:
            duration_ms = int((time.monotonic() - replace_started_at) * 1000)
        file_size_bytes = None
        if instance.image:
            try:
                file_size_bytes = instance.image.size
            except (OSError, ValueError):
                file_size_bytes = None
        log_event(
            logger,
            logging.INFO,
            "product_image.replace.committed",
            product_id=instance.product_id,
            product_image_id=instance.pk,
            old_object_key=old_key,
            new_object_key=new_key,
            file_size_bytes=file_size_bytes,
            duration_ms=duration_ms,
            request_id=get_request_id(),
        )
        schedule_product_image_delete_on_commit(
            object_key=old_key,
            product_id=instance.product_id,
            product_image_id=instance.pk,
            reason=DELETE_REASON_REPLACE,
        )


def _delete_download_file_on_row_delete(*, file_key: str, event_prefix: str) -> None:
    if not is_deletable_download_file_key(file_key):
        return
    try:
        delete_product_file(file_key=file_key)
        log_event(logger, logging.INFO, f"{event_prefix}.deleted", object_key=file_key)
    except ProductFileDeleteError as exc:
        log_event(
            logger,
            logging.WARNING,
            f"{event_prefix}.delete_failed",
            exc_info=exc,
            object_key=file_key,
            error_type=type(exc).__name__,
        )


@receiver(pre_delete, sender=ProductFile)
def delete_product_file_on_row_delete(sender, instance: ProductFile, **kwargs) -> None:
    _delete_download_file_on_row_delete(
        file_key=instance.file_key,
        event_prefix="product_file.row_delete",
    )


@receiver(pre_delete, sender=CustomGameFile)
def delete_custom_game_file_on_row_delete(sender, instance: CustomGameFile, **kwargs) -> None:
    _delete_download_file_on_row_delete(
        file_key=instance.file_key,
        event_prefix="custom_game_file.row_delete",
    )


@receiver(pre_delete, sender=ProductImage)
def schedule_product_image_delete_on_row_delete(sender, instance: ProductImage, **kwargs) -> None:
    object_key = instance.image.name
    if not object_key:
        return
    schedule_product_image_delete_on_commit(
        object_key=object_key,
        product_id=instance.product_id,
        product_image_id=instance.pk,
        reason=DELETE_REASON_ROW_DELETE,
    )
