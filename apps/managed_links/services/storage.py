from __future__ import annotations

import logging
import mimetypes
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core.logging import log_event
from apps.products.alerts import record_storage_unavailable_incident, resolve_storage_unavailable_incident
from apps.products.services.s3 import get_s3_client

logger = logging.getLogger("apps.managed_links.storage")

ASCII_PRINTABLE_MIN = 32
ASCII_PRINTABLE_MAX = 126


class ManagedLinkStorageError(Exception):
    pass


class ManagedLinkUploadError(ManagedLinkStorageError):
    pass


class ManagedLinkDeleteError(ManagedLinkStorageError):
    pass


class ManagedLinkMetadataError(ManagedLinkStorageError):
    pass


class ManagedLinkDownloadUrlError(ManagedLinkStorageError):
    pass


class ManagedLinkUploadUrlError(ManagedLinkStorageError):
    pass


class ManagedLinkStorageConfigurationError(ManagedLinkStorageError):
    pass


def _managed_links_bucket_name() -> str:
    bucket = (settings.S3_MANAGED_LINKS_BUCKET_NAME or "").strip()
    if not bucket:
        raise ManagedLinkStorageConfigurationError("S3_MANAGED_LINKS_BUCKET_NAME must be configured")
    return bucket


def _guess_content_type(filename: str) -> str:
    guessed_type, _ = mimetypes.guess_type(filename)
    return guessed_type or "application/octet-stream"


def _build_download_content_disposition(filename: str) -> str:
    safe_filename = Path(filename).name or "download"
    ascii_filename = "".join(
        character
        if ASCII_PRINTABLE_MIN <= ord(character) <= ASCII_PRINTABLE_MAX and character not in {'"', "\\"}
        else "_"
        for character in safe_filename
    )
    encoded_filename = quote(safe_filename, safe="")
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"


@lru_cache(maxsize=1)
def _allowed_upload_extensions() -> frozenset[str]:
    raw: str = settings.MANAGED_LINK_UPLOAD_ALLOWED_EXTENSIONS
    return frozenset(
        extension.strip().lower()
        for extension in raw.split(",") if extension.strip()
    )


def validate_upload_filename(filename: str) -> str:
    safe_name = Path(filename).name
    if not safe_name:
        raise ValidationError("Не удалось определить имя файла.")

    extension = Path(safe_name).suffix.lower()
    if extension not in _allowed_upload_extensions():
        raise ValidationError("Недопустимое расширение файла.")

    return safe_name


def build_managed_link_file_key(*, token: str, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"{token}/{uuid4().hex}{extension}"


def validate_managed_links_bucket_access() -> None:
    bucket_name = _managed_links_bucket_name()
    try:
        get_s3_client().head_bucket(Bucket=bucket_name)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "managed_link_storage.bucket.validation_failed",
            exc_info=exc,
            bucket_name=bucket_name,
            endpoint_url=settings.S3_ENDPOINT_URL,
            error_type=type(exc).__name__,
        )
        raise ManagedLinkStorageConfigurationError("Unable to access managed links S3 bucket") from exc

    log_event(
        logger,
        logging.INFO,
        "managed_link_storage.bucket.validated",
        bucket_name=bucket_name,
        endpoint_url=settings.S3_ENDPOINT_URL,
    )


def head_managed_link_file(*, file_key: str) -> dict:
    bucket_name = _managed_links_bucket_name()
    try:
        metadata = get_s3_client().head_object(Bucket=bucket_name, Key=file_key)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "managed_link_storage.metadata.failed",
            exc_info=exc,
            bucket_name=bucket_name,
            file_key=file_key,
            error_type=type(exc).__name__,
        )
        raise ManagedLinkMetadataError("Failed to read managed link file metadata from object storage") from exc

    log_event(
        logger,
        logging.INFO,
        "managed_link_storage.metadata.success",
        bucket_name=bucket_name,
        file_key=file_key,
        content_length=metadata.get("ContentLength"),
    )
    return metadata


def verify_uploaded_object(
    *,
    file_key: str,
    expected_size: int,
    content_type: str,
) -> dict[str, Any]:
    metadata = head_managed_link_file(file_key=file_key)

    actual_size = metadata.get("ContentLength", 0)
    if actual_size != expected_size:
        raise ValidationError("Размер загруженного файла не совпадает.")

    actual_type = metadata.get("ContentType") or ""
    if actual_type.strip().lower() != content_type.strip().lower():
        raise ValidationError("Content-Type загруженного файла не совпадает.")

    return metadata


def generate_presigned_upload_url(
    *,
    file_key: str,
    content_type: str,
    expires_seconds: int | None = None,
) -> dict[str, Any]:
    bucket_name = _managed_links_bucket_name()
    ttl = expires_seconds or settings.MANAGED_LINK_UPLOAD_PRESIGN_TTL_SECONDS
    try:
        url = get_s3_client().generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": bucket_name,
                "Key": file_key,
                "ContentType": content_type,
            },
            ExpiresIn=ttl,
        )
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "managed_link_storage.upload_url.failed",
            exc_info=exc,
            bucket_name=bucket_name,
            file_key=file_key,
            expires_seconds=ttl,
            error_type=type(exc).__name__,
        )
        record_storage_unavailable_incident(
            operation="managed_link_generate_presigned_upload_url",
            threshold=settings.STORAGE_INCIDENT_THRESHOLD,
            window_seconds=settings.STORAGE_INCIDENT_WINDOW_SECONDS,
        )
        raise ManagedLinkUploadUrlError("Failed to generate presigned managed link upload URL") from exc

    resolve_storage_unavailable_incident(operation="managed_link_generate_presigned_upload_url")
    return {
        "upload_url": url,
        "required_headers": {"Content-Type": content_type},
        "expires_in": ttl,
    }


def generate_presigned_download_url(
    *,
    file_key: str,
    original_filename: str,
    expires_seconds: int | None = None,
) -> str:
    bucket_name = _managed_links_bucket_name()
    ttl = expires_seconds or settings.S3_PRESIGNED_EXPIRE_SECONDS
    try:
        url = get_s3_client().generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": bucket_name,
                "Key": file_key,
                "ResponseContentDisposition": _build_download_content_disposition(original_filename),
            },
            ExpiresIn=ttl,
        )
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "managed_link_storage.download_url.failed",
            exc_info=exc,
            bucket_name=bucket_name,
            file_key=file_key,
            expires_seconds=ttl,
            error_type=type(exc).__name__,
        )
        record_storage_unavailable_incident(
            operation="managed_link_generate_presigned_download_url",
            threshold=settings.STORAGE_INCIDENT_THRESHOLD,
            window_seconds=settings.STORAGE_INCIDENT_WINDOW_SECONDS,
        )
        raise ManagedLinkDownloadUrlError("Failed to generate presigned managed link download URL") from exc

    resolve_storage_unavailable_incident(operation="managed_link_generate_presigned_download_url")
    return url


def delete_managed_link_file(*, file_key: str) -> None:
    bucket_name = _managed_links_bucket_name()
    try:
        get_s3_client().delete_object(Bucket=bucket_name, Key=file_key)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "managed_link_storage.delete.failed",
            exc_info=exc,
            bucket_name=bucket_name,
            file_key=file_key,
            error_type=type(exc).__name__,
        )
        raise ManagedLinkDeleteError("Failed to delete managed link file from object storage") from exc

    log_event(
        logger,
        logging.INFO,
        "managed_link_storage.delete.success",
        bucket_name=bucket_name,
        file_key=file_key,
    )
