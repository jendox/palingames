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


def migration_cache_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / ".product-image-migration-cache"


def migration_cache_path(object_key: str) -> Path:
    safe_name = object_key.replace("/", "__")
    return migration_cache_dir() / safe_name


def write_migration_cache(object_key: str, content: bytes) -> None:
    cache_path = migration_cache_path(object_key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)


def read_migration_cache(object_key: str) -> bytes | None:
    cache_path = migration_cache_path(object_key)
    if cache_path.is_file():
        return cache_path.read_bytes()
    return None


def unmigrated_reference_count(object_key: str) -> int:
    return ProductImage.objects.filter(image=object_key).count()


def delete_migration_cache(object_key: str) -> None:
    cache_path = migration_cache_path(object_key)
    if cache_path.is_file():
        cache_path.unlink()


def read_bytes_from_migrated_peer(*, order: int) -> bytes | None:
    """Reuse bytes from an already-migrated preview with the same display order."""
    prefix = preview_storage_prefix()
    peer_keys = (
        ProductImage.objects.filter(image__startswith=f"{prefix}/", order=order)
        .order_by("id")
        .values_list("image", flat=True)
    )
    for peer_key in peer_keys:
        try:
            response = product_image_storage.get_s3_client().get_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=peer_key,
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                continue
            raise ProductImageMigrationError(f"Failed to read migrated preview: {peer_key}") from exc
        except (BotoCoreError, ValueError) as exc:
            raise ProductImageMigrationError(f"Failed to read migrated preview: {peer_key}") from exc
        else:
            return response["Body"].read()
    return None


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


def read_s3_object_bytes(object_key: str) -> bytes:
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


def read_missing_source_with_fallback(object_key: str, *, order: int | None) -> bytes:
    if order is None:
        raise ProductImageMigrationError(f"Image source not found: {object_key}")

    peer_bytes = read_bytes_from_migrated_peer(order=order)
    if peer_bytes is None:
        raise ProductImageMigrationError(f"Image source not found: {object_key}")
    return peer_bytes


def read_product_image_bytes(object_key: str, *, order: int | None = None) -> bytes:
    cached = read_migration_cache(object_key)
    if cached is not None:
        return cached

    local_path = Path(settings.MEDIA_ROOT) / object_key
    if local_path.is_file():
        content = local_path.read_bytes()
        write_migration_cache(object_key, content)
        return content

    try:
        content = read_s3_object_bytes(object_key)
    except ProductImageMigrationError as exc:
        if "Image source not found" not in str(exc):
            raise
        content = read_missing_source_with_fallback(object_key, order=order)

    write_migration_cache(object_key, content)
    return content


def delete_old_product_image_source(object_key: str) -> None:
    if unmigrated_reference_count(object_key) > 0:
        return

    local_path = Path(settings.MEDIA_ROOT) / object_key
    if local_path.is_file():
        local_path.unlink()

    try:
        product_image_storage.get_s3_client().delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            delete_migration_cache(object_key)
            return
        raise ProductImageMigrationError(f"Failed to delete old image object: {object_key}") from exc
    except (BotoCoreError, ValueError) as exc:
        raise ProductImageMigrationError(f"Failed to delete old image object: {object_key}") from exc

    delete_migration_cache(object_key)


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

    content_bytes = read_product_image_bytes(old_key, order=image.order)
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
