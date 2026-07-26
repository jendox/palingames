from __future__ import annotations

import logging
import time
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from kombu.exceptions import OperationalError as KombuOperationalError

from apps.core.logging import get_request_id, log_event
from apps.products.models import ProductImage
from apps.products.services.s3 import get_s3_client

logger = logging.getLogger("apps.products.storage")

DELETE_REASON_REPLACE = "replace"
DELETE_REASON_ROW_DELETE = "row_delete"


class ProductImageDeleteError(Exception):
    pass


def is_product_image_key_referenced(object_key: str) -> bool:
    if not object_key:
        return False
    return ProductImage.objects.filter(image=object_key).exists()


def _storage_backend_label() -> str:
    if settings.S3_PRODUCT_IMAGES_ENABLED:
        return "ProductImageS3Storage"
    return "FileSystemStorage"


def _is_not_found_error(exc: ClientError) -> bool:
    error_code = exc.response.get("Error", {}).get("Code")
    return error_code in {"404", "NoSuchKey", "NotFound"}


def _delete_local_product_image(object_key: str) -> str:
    local_path = Path(settings.MEDIA_ROOT) / object_key
    if not local_path.is_file():
        return "not_found"
    local_path.unlink()
    return "deleted"


def _delete_s3_product_image(object_key: str) -> str:
    get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
    return "deleted"


def _log_delete_failure(
    *,
    exc: Exception,
    object_key: str,
    product_id: int | None,
    product_image_id: int | None,
    duration_ms: int,
    attempt_number: int,
) -> None:
    log_event(
        logger,
        logging.ERROR,
        "product_image.delete.failed",
        exc_info=exc,
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        duration_ms=duration_ms,
        attempt_number=attempt_number,
        storage_backend=_storage_backend_label(),
        error_type=type(exc).__name__,
        error_message=str(exc),
    )


def _log_delete_not_found(
    *,
    object_key: str,
    product_id: int | None,
    product_image_id: int | None,
    duration_ms: int,
    attempt_number: int,
    error_type: str | None = None,
) -> None:
    log_event(
        logger,
        logging.INFO,
        "product_image.delete.not_found",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        duration_ms=duration_ms,
        attempt_number=attempt_number,
        storage_backend=_storage_backend_label(),
        error_type=error_type,
    )


def _execute_product_image_delete(
    *,
    object_key: str,
    product_id: int | None,
    product_image_id: int | None,
    attempt_number: int,
    started_at: float,
) -> str:
    try:
        if settings.S3_PRODUCT_IMAGES_ENABLED:
            return _delete_s3_product_image(object_key)
        result = _delete_local_product_image(object_key)
        if result == "not_found":
            duration_ms = int((time.monotonic() - started_at) * 1000)
            _log_delete_not_found(
                object_key=object_key,
                product_id=product_id,
                product_image_id=product_image_id,
                duration_ms=duration_ms,
                attempt_number=attempt_number,
            )
        return result
    except ClientError as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        if _is_not_found_error(exc):
            _log_delete_not_found(
                object_key=object_key,
                product_id=product_id,
                product_image_id=product_image_id,
                duration_ms=duration_ms,
                attempt_number=attempt_number,
                error_type=type(exc).__name__,
            )
            return "not_found"
        _log_delete_failure(
            exc=exc,
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            duration_ms=duration_ms,
            attempt_number=attempt_number,
        )
        raise ProductImageDeleteError(f"Failed to delete product image object: {object_key}") from exc
    except (BotoCoreError, OSError, ValueError) as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _log_delete_failure(
            exc=exc,
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            duration_ms=duration_ms,
            attempt_number=attempt_number,
        )
        raise ProductImageDeleteError(f"Failed to delete product image object: {object_key}") from exc


def delete_product_image_object(
    *,
    object_key: str,
    product_id: int | None = None,
    product_image_id: int | None = None,
    attempt_number: int = 1,
) -> str:
    """
    Delete a product preview object from storage.

    Returns one of: ``deleted``, ``not_found``, ``skipped_in_use``.
    """
    if not object_key:
        return "skipped_in_use"

    if is_product_image_key_referenced(object_key):
        log_event(
            logger,
            logging.INFO,
            "product_image.delete.skipped_in_use",
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            storage_backend=_storage_backend_label(),
        )
        return "skipped_in_use"

    started_at = time.monotonic()
    log_event(
        logger,
        logging.INFO,
        "product_image.delete.started",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        attempt_number=attempt_number,
        storage_backend=_storage_backend_label(),
        endpoint_url=settings.S3_ENDPOINT_URL if settings.S3_PRODUCT_IMAGES_ENABLED else None,
    )

    result = _execute_product_image_delete(
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        attempt_number=attempt_number,
        started_at=started_at,
    )
    if result == "not_found":
        return "not_found"

    duration_ms = int((time.monotonic() - started_at) * 1000)
    log_event(
        logger,
        logging.INFO,
        "product_image.delete.succeeded",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        duration_ms=duration_ms,
        attempt_number=attempt_number,
        storage_backend=_storage_backend_label(),
    )
    return result


def enqueue_product_image_delete(
    *,
    object_key: str,
    product_id: int | None = None,
    product_image_id: int | None = None,
    reason: str,
) -> None:
    from apps.products.tasks import delete_product_image_task

    log_event(
        logger,
        logging.INFO,
        "product_image.delete.scheduled",
        object_key=object_key,
        product_id=product_id,
        product_image_id=product_image_id,
        reason=reason,
        request_id=get_request_id(),
    )
    try:
        delete_product_image_task.delay(
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            reason=reason,
        )
    except KombuOperationalError as exc:
        # DB commit already succeeded; do not fail the HTTP response.
        log_event(
            logger,
            logging.ERROR,
            "product_image.delete.enqueue_failed",
            exc_info=exc,
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            reason=reason,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def schedule_product_image_delete_on_commit(
    *,
    object_key: str,
    product_id: int | None = None,
    product_image_id: int | None = None,
    reason: str,
) -> None:
    transaction.on_commit(
        lambda: enqueue_product_image_delete(
            object_key=object_key,
            product_id=product_id,
            product_image_id=product_image_id,
            reason=reason,
        ),
    )
