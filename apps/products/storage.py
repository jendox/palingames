from __future__ import annotations

import logging
import mimetypes
import time
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

from apps.core.logging import log_event
from apps.products.services.s3 import ProductStorageConfigurationError, get_s3_client

logger = logging.getLogger("apps.products.storage")


def build_product_image_object_key(*, product_slug, filename: str) -> str:
    extension = Path(filename).suffix.lower() or ".jpg"
    prefix = settings.S3_PRODUCT_IMAGES_PREFIX.strip("/")
    slug = product_slug or "product"
    return f"{prefix}/{slug}/{uuid4().hex}{extension}"


def build_collection_cover_object_key(*, collection_slug, filename: str) -> str:
    extension = Path(filename).suffix.lower() or ".jpg"
    prefix = settings.S3_COLLECTION_COVER_PREFIX.strip("/")
    slug = collection_slug or "cover"
    return f"{prefix}/{slug}/{uuid4().hex}{extension}"


def build_product_image_public_url(object_key: str) -> str:
    base_url = (settings.S3_PRODUCT_IMAGES_PUBLIC_BASE_URL or "").strip()
    if base_url:
        return f"{base_url.rstrip('/')}/{object_key.lstrip('/')}"
    endpoint = settings.S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.S3_BUCKET_NAME
    if settings.S3_ADDRESSING_STYLE == "path":
        return f"{endpoint}/{bucket}/{object_key.lstrip('/')}"
    return f"{endpoint}/{object_key.lstrip('/')}"


def _storage_backend_label() -> str:
    return "ProductImageS3Storage"


def _file_size_bytes(content: File) -> int | None:
    size = getattr(content, "size", None)
    if size is not None:
        return int(size)
    try:
        current_position = content.tell()
        content.seek(0, 2)
        size = content.tell()
        content.seek(current_position)
        return int(size)
    except (OSError, ValueError, AttributeError):
        return None


@deconstructible
class ProductImageS3Storage(Storage):
    def _open(self, name: str, mode: str = "rb") -> NoReturn:
        raise NotImplementedError("Product images are write-once objects in S3")

    def _save(self, name: str, content: File) -> str:
        mime_type = (
            getattr(content, "content_type", None)
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        file_size_bytes = _file_size_bytes(content)
        started_at = time.monotonic()
        log_event(
            logger,
            logging.INFO,
            "product_image.upload.started",
            object_key=name,
            file_size_bytes=file_size_bytes,
            content_type=mime_type,
            storage_backend=_storage_backend_label(),
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        try:
            get_s3_client().upload_fileobj(
                Fileobj=content,
                Bucket=settings.S3_BUCKET_NAME,
                Key=name,
                ExtraArgs={
                    "ContentType": mime_type,
                    "CacheControl": "public, max-age=31536000, immutable",
                },
            )
        except (ClientError, BotoCoreError, OSError, ValueError) as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            log_event(
                logger,
                logging.ERROR,
                "product_image.upload.failed",
                exc_info=exc,
                object_key=name,
                file_size_bytes=file_size_bytes,
                content_type=mime_type,
                duration_ms=duration_ms,
                storage_backend=_storage_backend_label(),
                endpoint_url=settings.S3_ENDPOINT_URL,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            log_event(
                logger,
                logging.ERROR,
                "product_image.replace.failed",
                object_key=name,
                file_size_bytes=file_size_bytes,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        duration_ms = int((time.monotonic() - started_at) * 1000)
        log_event(
            logger,
            logging.INFO,
            "product_image.upload.succeeded",
            object_key=name,
            file_size_bytes=file_size_bytes,
            content_type=mime_type,
            duration_ms=duration_ms,
            storage_backend=_storage_backend_label(),
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        return name

    def delete(self, name):
        if not name:
            return
        started_at = time.monotonic()
        log_event(
            logger,
            logging.INFO,
            "product_image.delete.started",
            object_key=name,
            storage_backend=_storage_backend_label(),
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        try:
            get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=name)
        except ClientError as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                log_event(
                    logger,
                    logging.INFO,
                    "product_image.delete.not_found",
                    object_key=name,
                    duration_ms=duration_ms,
                    storage_backend=_storage_backend_label(),
                    error_type=type(exc).__name__,
                )
                return
            log_event(
                logger,
                logging.ERROR,
                "product_image.delete.failed",
                exc_info=exc,
                object_key=name,
                duration_ms=duration_ms,
                storage_backend=_storage_backend_label(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise ProductStorageConfigurationError("Failed to delete product image from object storage") from exc
        except (BotoCoreError, ValueError) as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            log_event(
                logger,
                logging.ERROR,
                "product_image.delete.failed",
                exc_info=exc,
                object_key=name,
                duration_ms=duration_ms,
                storage_backend=_storage_backend_label(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise ProductStorageConfigurationError("Failed to delete product image from object storage") from exc

        duration_ms = int((time.monotonic() - started_at) * 1000)
        log_event(
            logger,
            logging.INFO,
            "product_image.delete.succeeded",
            object_key=name,
            duration_ms=duration_ms,
            storage_backend=_storage_backend_label(),
        )

    def exists(self, name):
        try:
            get_s3_client().head_object(Bucket=settings.S3_BUCKET_NAME, Key=name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        except (BotoCoreError, ValueError):
            return False
        return True

    def url(self, name: str | None) -> str | None:
        if name is None:
            return None
        return build_product_image_public_url(name)

    def size(self, name: str) -> int:
        try:
            metadata = get_s3_client().head_object(Bucket=settings.S3_BUCKET_NAME, Key=name)
        except (ClientError, BotoCoreError, ValueError):
            return 0
        return int(metadata.get("ContentLength") or 0)

    def get_available_name(self, name: str, max_length: int | None = None) -> str:
        if max_length is not None and len(name) > max_length:
            from django.core.exceptions import SuspiciousFileOperation

            raise SuspiciousFileOperation(
                f"Storage key '{name}' exceeds the maximum permitted length ({max_length}).",
            )
        return name


def get_product_image_storage() -> Storage:
    if settings.S3_PRODUCT_IMAGES_ENABLED:
        return ProductImageS3Storage()
    return FileSystemStorage()
