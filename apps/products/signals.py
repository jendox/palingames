from __future__ import annotations

import logging

from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from apps.core.logging import log_event
from apps.custom_games.models import CustomGameFile
from apps.products.models import ProductFile, ProductImage
from apps.products.services.s3 import (
    ProductFileDeleteError,
    delete_product_file,
    is_deletable_download_file_key,
)

logger = logging.getLogger("apps.products.storage")


@receiver(pre_save, sender=ProductImage)
def delete_replaced_product_image(sender, instance: ProductImage, **kwargs) -> None:
    if not instance.pk:
        return
    try:
        old = ProductImage.objects.only("image").get(pk=instance.pk)
    except ProductImage.DoesNotExist:
        return

    old_name = old.image.name
    new_name = instance.image.name if instance.image else ""
    if not old_name or old_name == new_name:
        return

    try:
        old.image.storage.delete(old_name)
        log_event(logger, logging.INFO, "product_image.replace.deleted_old", object_key=old_name)
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "product_image.replace.delete_old_failed",
            exc_info=exc,
            object_key=old_name,
            error_type=type(exc).__name__,
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
def delete_product_image_on_row_delete(sender, instance: ProductImage, **kwargs) -> None:
    name = instance.image.name
    if not name:
        return
    try:
        instance.image.storage.delete(name)
        log_event(logger, logging.INFO, "product_image.row_delete.deleted", object_key=name)
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "product_image.row_delete.delete_failed",
            exc_info=exc,
            object_key=name,
            error_type=type(exc).__name__,
        )
