from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import QuerySet

from apps.core.logging import log_event
from apps.products import storage as product_image_storage
from apps.products.models import ProductImage
from apps.products.storage import ProductImageS3Storage, build_product_image_object_key

logger = logging.getLogger("apps.products.storage")


class ProductImageMigrationError(Exception):
    pass


@dataclass(frozen=True)
class ProductImageMigrationOutcome:
    status: str
    old_key: str
    new_key: str | None = None
    reason: str = ""


def preview_storage_prefix() -> str:
    return settings.S3_PRODUCT_IMAGES_PREFIX.strip("/")


def is_migrated_image_key(object_key: str) -> bool:
    prefix = preview_storage_prefix()
    return object_key.startswith(f"{prefix}/")


def product_images_to_migrate_queryset(
    *,
    product_slug: str | None = None,
    limit: int | None = None,
) -> QuerySet[ProductImage]:
    prefix = preview_storage_prefix()
    queryset = (
        ProductImage.objects.select_related("product")
        .exclude(image="")
        .exclude(image__startswith=f"{prefix}/")
        .order_by("id")
    )
    if product_slug:
        queryset = queryset.filter(product__slug=product_slug)
    if limit is not None:
        queryset = queryset[:limit]
    return queryset


def read_product_image_bytes(object_key: str) -> bytes:
    local_path = Path(settings.MEDIA_ROOT) / object_key
    if local_path.is_file():
        return local_path.read_bytes()

    try:
        response = product_image_storage.get_s3_client().get_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=object_key,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise ProductImageMigrationError(f"Image source not found: {object_key}") from exc
        raise ProductImageMigrationError(f"Failed to read image from object storage: {object_key}") from exc
    except (BotoCoreError, ValueError) as exc:
        raise ProductImageMigrationError(f"Failed to read image from object storage: {object_key}") from exc
    else:
        return response["Body"].read()


def delete_old_product_image_source(object_key: str) -> None:
    local_path = Path(settings.MEDIA_ROOT) / object_key
    if local_path.is_file():
        local_path.unlink()

    try:
        product_image_storage.get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return
        raise ProductImageMigrationError(f"Failed to delete old image object: {object_key}") from exc
    except (BotoCoreError, ValueError) as exc:
        raise ProductImageMigrationError(f"Failed to delete old image object: {object_key}") from exc


def migrate_product_image(image: ProductImage, *, dry_run: bool = False) -> ProductImageMigrationOutcome:
    old_key = image.image.name
    if not old_key:
        return ProductImageMigrationOutcome(status="skipped", old_key="", reason="empty_name")

    if is_migrated_image_key(old_key):
        return ProductImageMigrationOutcome(status="skipped", old_key=old_key, reason="already_migrated")

    new_key = build_product_image_object_key(
        product_slug=image.product.slug,
        filename=Path(old_key).name,
    )

    if dry_run:
        return ProductImageMigrationOutcome(status="dry_run", old_key=old_key, new_key=new_key)

    content_bytes = read_product_image_bytes(old_key)
    content = ContentFile(content_bytes, name=Path(old_key).name)
    storage = ProductImageS3Storage()
    saved_key = storage._save(new_key, content)
    ProductImage.objects.filter(pk=image.pk).update(image=saved_key)
    delete_old_product_image_source(old_key)

    log_event(
        logger,
        logging.INFO,
        "product_image.migration.completed",
        product_image_id=image.pk,
        product_slug=image.product.slug,
        old_key=old_key,
        new_key=saved_key,
    )
    return ProductImageMigrationOutcome(status="migrated", old_key=old_key, new_key=saved_key)


def migrate_product_images(
    *,
    dry_run: bool = False,
    product_slug: str | None = None,
    limit: int | None = None,
) -> list[ProductImageMigrationOutcome]:
    outcomes: list[ProductImageMigrationOutcome] = []
    for image in product_images_to_migrate_queryset(product_slug=product_slug, limit=limit):
        outcomes.append(migrate_product_image(image, dry_run=dry_run))
    return outcomes
