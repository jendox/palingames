from __future__ import annotations

import hashlib
import logging
import mimetypes
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.core.logging import log_event

logger = logging.getLogger("apps.products.storage")
ASCII_PRINTABLE_MIN = 32
ASCII_PRINTABLE_MAX = 126


class ProductFileStorageError(Exception):
    pass


class ProductFileUploadError(ProductFileStorageError):
    pass


class ProductFileDeleteError(ProductFileStorageError):
    pass


class ProductFileMetadataError(ProductFileStorageError):
    pass


class ProductFileDownloadUrlError(ProductFileStorageError):
    pass


class ProductStorageConfigurationError(ProductFileStorageError):
    pass


def _guess_content_type(filename: str) -> str:
    guessed_type, _ = mimetypes.guess_type(filename)
    return guessed_type or "application/octet-stream"


def _reset_uploaded_file(uploaded_file: UploadedFile) -> None:
    if getattr(uploaded_file, "closed", False):
        return
    uploaded_file.seek(0)


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


@lru_cache(maxsize=2)
def get_s3_client():
    if not settings.S3_BUCKET_NAME:
        raise ProductStorageConfigurationError("S3_BUCKET_NAME must be configured")

    client = boto3.client(
        service_name="s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        region_name=settings.S3_REGION_NAME,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        use_ssl=settings.S3_USE_SSL,
        config=Config(
            signature_version="s3v4",
            connect_timeout=settings.S3_CONNECT_TIMEOUT_SECONDS,
            read_timeout=settings.S3_READ_TIMEOUT_SECONDS,
            max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
            retries={
                "mode": "standard",
                "max_attempts": settings.S3_RETRY_MAX_ATTEMPTS,
            },
            s3={
                "addressing_style": settings.S3_ADDRESSING_STYLE,
            },
        ),
    )
    log_event(
        logger,
        logging.INFO,
        "product_storage.client.created",
        endpoint_url=settings.S3_ENDPOINT_URL,
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION_NAME,
        use_ssl=settings.S3_USE_SSL,
        addressing_style=settings.S3_ADDRESSING_STYLE,
    )
    return client


def build_product_file_key(*, product_slug: str, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"{product_slug}/{uuid4().hex}{extension}"


def _calculate_sha256(uploaded_file: UploadedFile) -> str:
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    _reset_uploaded_file(uploaded_file)
    return digest.hexdigest()


def validate_storage_bucket_access() -> None:
    try:
        get_s3_client().head_bucket(Bucket=settings.S3_BUCKET_NAME)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "product_storage.bucket.validation_failed",
            exc_info=exc,
            bucket_name=settings.S3_BUCKET_NAME,
            endpoint_url=settings.S3_ENDPOINT_URL,
            error_type=type(exc).__name__,
        )
        raise ProductStorageConfigurationError("Unable to access configured S3 bucket") from exc

    log_event(
        logger,
        logging.INFO,
        "product_storage.bucket.validated",
        bucket_name=settings.S3_BUCKET_NAME,
        endpoint_url=settings.S3_ENDPOINT_URL,
    )


def upload_product_file(*, product_slug: str, uploaded_file: UploadedFile) -> dict[str, str | int]:
    file_key = build_product_file_key(product_slug=product_slug, filename=uploaded_file.name)
    mime_type = uploaded_file.content_type or _guess_content_type(uploaded_file.name)
    checksum_sha256 = _calculate_sha256(uploaded_file)

    log_event(
        logger,
        logging.INFO,
        "product_file.upload.started",
        bucket_name=settings.S3_BUCKET_NAME,
        product_slug=product_slug,
        file_key=file_key,
        original_filename=uploaded_file.name,
        mime_type=mime_type,
        size_bytes=uploaded_file.size,
    )

    try:
        get_s3_client().upload_fileobj(
            Fileobj=uploaded_file,
            Bucket=settings.S3_BUCKET_NAME,
            Key=file_key,
            ExtraArgs={
                "ContentType": mime_type,
            },
        )
    except (ClientError, BotoCoreError, OSError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "product_file.upload.failed",
            exc_info=exc,
            bucket_name=settings.S3_BUCKET_NAME,
            product_slug=product_slug,
            file_key=file_key,
            original_filename=uploaded_file.name,
            mime_type=mime_type,
            size_bytes=uploaded_file.size,
            error_type=type(exc).__name__,
        )
        raise ProductFileUploadError("Failed to upload product file to object storage") from exc

    log_event(
        logger,
        logging.INFO,
        "product_file.upload.success",
        bucket_name=settings.S3_BUCKET_NAME,
        product_slug=product_slug,
        file_key=file_key,
        original_filename=uploaded_file.name,
        mime_type=mime_type,
        size_bytes=uploaded_file.size,
    )

    return {
        "file_key": file_key,
        "original_filename": Path(uploaded_file.name).name,
        "mime_type": mime_type,
        "size_bytes": uploaded_file.size,
        "checksum_sha256": checksum_sha256,
    }


def delete_product_file(*, file_key: str) -> None:
    try:
        get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=file_key)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "product_file.delete.failed",
            exc_info=exc,
            bucket_name=settings.S3_BUCKET_NAME,
            file_key=file_key,
            error_type=type(exc).__name__,
        )
        raise ProductFileDeleteError("Failed to delete product file from object storage") from exc

    log_event(
        logger,
        logging.INFO,
        "product_file.delete.success",
        bucket_name=settings.S3_BUCKET_NAME,
        file_key=file_key,
    )


def head_product_file(*, file_key: str) -> dict:
    try:
        metadata = get_s3_client().head_object(Bucket=settings.S3_BUCKET_NAME, Key=file_key)
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "product_file.metadata.failed",
            exc_info=exc,
            bucket_name=settings.S3_BUCKET_NAME,
            file_key=file_key,
            error_type=type(exc).__name__,
        )
        raise ProductFileMetadataError("Failed to read product file metadata from object storage") from exc

    log_event(
        logger,
        logging.INFO,
        "product_file.metadata.success",
        bucket_name=settings.S3_BUCKET_NAME,
        file_key=file_key,
        content_length=metadata.get("ContentLength"),
        etag=metadata.get("ETag"),
    )
    return metadata


def generate_presigned_download_url(
    *,
    file_key: str,
    original_filename: str,
    expires_seconds: int | None = None,
) -> str:
    ttl = expires_seconds or settings.S3_PRESIGNED_EXPIRE_SECONDS
    try:
        url = get_s3_client().generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.S3_BUCKET_NAME,
                "Key": file_key,
                "ResponseContentDisposition": _build_download_content_disposition(original_filename),
            },
            ExpiresIn=ttl,
        )
    except (ClientError, BotoCoreError, ValueError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "product_file.download_url.failed",
            exc_info=exc,
            bucket_name=settings.S3_BUCKET_NAME,
            file_key=file_key,
            expires_seconds=ttl,
            error_type=type(exc).__name__,
        )
        raise ProductFileDownloadUrlError("Failed to generate presigned product download URL") from exc

    log_event(
        logger,
        logging.INFO,
        "product_file.download_url.generated",
        bucket_name=settings.S3_BUCKET_NAME,
        file_key=file_key,
        expires_seconds=ttl,
    )
    return url
