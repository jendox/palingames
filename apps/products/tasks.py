from __future__ import annotations

import logging

from celery import Task, shared_task
from django.conf import settings

from apps.core.logging import log_event
from apps.products.services.product_image_cleanup import ProductImageDeleteError, delete_product_image_object
from apps.products.services.s3 import ProductStorageConfigurationError

logger = logging.getLogger("apps.products.tasks")


@shared_task(
    bind=True,
    autoretry_for=(ProductImageDeleteError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": settings.PRODUCT_IMAGE_DELETE_TASK_MAX_RETRIES},
    dont_autoretry_for=(ProductStorageConfigurationError,),
)
def delete_product_image_task(
    self: Task,
    *,
    object_key: str,
    product_id: int | None = None,
    product_image_id: int | None = None,
    reason: str = "unspecified",
) -> str:
    attempt_number = self.request.retries + 1
    log_event(
        logger,
        logging.INFO,
        "product_image.delete.task.started",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        reason=reason,
        attempt_number=attempt_number,
        task_id=self.request.id,
    )
    try:
        result = delete_product_image_object(
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            attempt_number=attempt_number,
        )
    except ProductImageDeleteError:
        log_event(
            logger,
            logging.WARNING,
            "product_image.delete.task.failed",
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            reason=reason,
            attempt_number=attempt_number,
            task_id=self.request.id,
        )
        raise

    log_event(
        logger,
        logging.INFO,
        "product_image.delete.task.finished",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        reason=reason,
        result=result,
        attempt_number=attempt_number,
        task_id=self.request.id,
    )
    return result
