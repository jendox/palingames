from __future__ import annotations

import hashlib
import mimetypes
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile


def _guess_content_type(filename: str) -> str:
    guessed_type, _ = mimetypes.guess_type(filename)
    return guessed_type or "application/octet-stream"


def _reset_uploaded_file(uploaded_file: UploadedFile) -> None:
    if getattr(uploaded_file, "closed", False):
        return
    uploaded_file.seek(0)


@lru_cache(maxsize=2)
def get_s3_client():
    return boto3.client(
        service_name="s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        region_name=settings.S3_REGION_NAME,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        use_ssl=settings.S3_USE_SSL,
        config=Config(
            signature_version="s3v4",
            s3={
                "addressing_style": settings.S3_ADDRESSING_STYLE,
            },
        ),
    )


def build_product_file_key(*, product_slug: str, filename: str) -> str:
    extension = Path(filename).suffix.lower()
    return f"{product_slug}/{uuid4().hex}{extension}"


def _calculate_sha256(uploaded_file: UploadedFile) -> str:
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    _reset_uploaded_file(uploaded_file)
    return digest.hexdigest()


def upload_product_file(*, product_slug: str, uploaded_file: UploadedFile) -> dict[str, str | int]:
    file_key = build_product_file_key(product_slug=product_slug, filename=uploaded_file.name)
    mime_type = uploaded_file.content_type or _guess_content_type(uploaded_file.name)
    checksum_sha256 = _calculate_sha256(uploaded_file)

    get_s3_client().upload_fileobj(
        Fileobj=uploaded_file,
        Bucket=settings.S3_BUCKET_NAME,
        Key=file_key,
        ExtraArgs={
            "ContentType": mime_type,
        },
    )

    return {
        "file_key": file_key,
        "original_filename": Path(uploaded_file.name).name,
        "mime_type": mime_type,
        "size_bytes": uploaded_file.size,
        "checksum_sha256": checksum_sha256,
    }


def delete_product_file(*, file_key: str) -> None:
    get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=file_key)


def head_product_file(*, file_key: str) -> dict:
    return get_s3_client().head_object(Bucket=settings.S3_BUCKET_NAME, Key=file_key)


def generate_presigned_download_url(
    *,
    file_key: str,
    original_filename: str,
    expires_seconds: int | None = None,
) -> str:
    return get_s3_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.S3_BUCKET_NAME,
            "Key": file_key,
            "ResponseContentDisposition": f'attachment; filename="{original_filename}"',
        },
        ExpiresIn=expires_seconds or settings.S3_PRESIGNED_EXPIRE_SECONDS,
    )
