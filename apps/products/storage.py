from __future__ import annotations

import logging
import mimetypes
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


def build_product_image_public_url(object_key: str) -> str:
    base_url = (settings.S3_PRODUCT_IMAGES_PUBLIC_BASE_URL or "").strip()
    if base_url:
        return f"{base_url.rstrip('/')}/{object_key.lstrip('/')}"
    endpoint = settings.S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.S3_BUCKET_NAME
    if settings.S3_ADDRESSING_STYLE == "path":
        return f"{endpoint}/{bucket}/{object_key.lstrip('/')}"
    return f"{endpoint}/{object_key.lstrip('/')}"


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
            log_event(
                logger,
                logging.ERROR,
                "product_image.upload.failed",
                exc_info=exc,
                object_key=name,
                error_type=type(exc).__name__,
            )
            raise

        log_event(logger, logging.INFO, "product_image.upload.success", object_key=name)
        return name

    def delete(self, name):
        if not name:
            return
        try:
            get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=name)
        except (ClientError, BotoCoreError, ValueError) as exc:
            log_event(
                logger,
                logging.ERROR,
                "product_image.delete.failed",
                exc_info=exc,
                object_key=name,
                error_type=type(exc).__name__,
            )
            raise ProductStorageConfigurationError("Failed to delete product image from object storage") from exc

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
