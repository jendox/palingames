from __future__ import annotations

import hashlib
import io
import logging
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core.logging import log_event
from apps.products.models import ProductImage
from apps.products.services.product_image_cleanup import (
    DELETE_REASON_THUMB_REGENERATE,
    schedule_product_image_delayed_delete_on_commit,
    schedule_product_image_delete_on_commit,
)
from apps.products.services.s3 import get_s3_client
from apps.products.storage import build_product_image_public_url, get_product_image_storage

logger = logging.getLogger("apps.products.storage")

ThumbnailResult = Literal["generated", "skipped", "missing_source", "failed"]
TARGET_WIDTHS = (400, 800)
LISTING_THUMB_WIDTH = TARGET_WIDTHS[0]
WEBP_QUALITY = 80
HTTP_STATUS_SERVER_ERROR = 500
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class ProductImageThumbnailTransientError(Exception):
    """Temporary failure (network / 5xx) — Celery should retry."""


@dataclass(frozen=True)
class _PreparedVariant:
    display_width: int
    content: bytes
    object_key: str


def _is_transient_client_error(exc: ClientError) -> bool:
    error_code = exc.response.get("Error", {}).get("Code") or ""
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if status and int(status) >= HTTP_STATUS_SERVER_ERROR:
        return True
    return error_code in {"SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable"}


def read_product_image_source_bytes(source_key: str) -> bytes:  # noqa: C901
    if not source_key:
        raise ProductImageThumbnailTransientError("empty source key")

    if settings.S3_PRODUCT_IMAGES_ENABLED:
        try:
            response = get_s3_client().get_object(Bucket=settings.S3_BUCKET_NAME, Key=source_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise ProductImageMigrationMissingSource(source_key) from exc
            if _is_transient_client_error(exc):
                raise ProductImageThumbnailTransientError(str(exc)) from exc
            raise ProductImageThumbnailTransientError(str(exc)) from exc
        except BotoCoreError as exc:
            raise ProductImageThumbnailTransientError(str(exc)) from exc
        else:
            return response["Body"].read()

    local_path = Path(settings.MEDIA_ROOT) / source_key
    if not local_path.is_file():
        raise ProductImageMigrationMissingSource(source_key)
    return local_path.read_bytes()


class ProductImageMigrationMissingSource(Exception):
    def __init__(self, source_key: str) -> None:
        self.source_key = source_key
        super().__init__(source_key)


def _source_stem(source_key: str) -> str:
    return str(Path(source_key).with_suffix(""))


def _deterministic_key(source_key: str, width: int) -> str:
    return f"{_source_stem(source_key)}_w{width}.webp"


def _versioned_key(source_key: str, width: int, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()[:8]
    return f"{_source_stem(source_key)}_w{width}_{digest}.webp"


def _object_exists(object_key: str) -> bool:
    if settings.S3_PRODUCT_IMAGES_ENABLED:
        return get_product_image_storage().exists(object_key)
    return (Path(settings.MEDIA_ROOT) / object_key).is_file()


def _upload_webp(object_key: str, content: bytes) -> None:
    buffer = io.BytesIO(content)
    if settings.S3_PRODUCT_IMAGES_ENABLED:
        get_s3_client().upload_fileobj(
            Fileobj=buffer,
            Bucket=settings.S3_BUCKET_NAME,
            Key=object_key,
            ExtraArgs={"ContentType": "image/webp", "CacheControl": IMMUTABLE_CACHE_CONTROL},
        )
        return
    target = Path(settings.MEDIA_ROOT) / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _delete_uploaded_keys(keys: set[str]) -> None:
    for key in keys:
        if not key:
            continue
        schedule_product_image_delete_on_commit(
            object_key=key,
            product_id=None,
            product_image_id=None,
            reason="orphan_thumbnail",
        )


def _prepare_image(source_bytes: bytes) -> tuple[Image.Image, int, int]:
    max_bytes = getattr(settings, "PRODUCT_IMAGE_THUMB_MAX_SOURCE_BYTES", 50 * 1024 * 1024)
    if len(source_bytes) > max_bytes:
        raise ValueError("source too large")

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        img = Image.open(io.BytesIO(source_bytes))
        img.load()

    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)

    img = ImageOps.exif_transpose(img)
    if img.mode == "CMYK":
        img = img.convert("RGB")
    elif img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    return img, img.width, img.height


def _encode_webp(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    save_kwargs = {"format": "WEBP", "quality": WEBP_QUALITY, "method": 6}
    if img.mode == "RGBA":
        save_kwargs["exact"] = True
    img.save(buffer, **save_kwargs)
    return buffer.getvalue()


def _resize_copy(img: Image.Image, target_width: int) -> tuple[Image.Image, int]:
    if img.width <= target_width:
        return img.copy(), img.width
    ratio = target_width / img.width
    new_height = max(1, round(img.height * ratio))
    resized = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
    return resized, target_width


def _choose_object_key(
    *,
    source_key: str,
    width: int,
    content: bytes,
    use_versioned: bool,
) -> str:
    deterministic = _deterministic_key(source_key, width)
    if use_versioned or _object_exists(deterministic):
        return _versioned_key(source_key, width, content)
    return deterministic


def _planned_keys_match_row(
    row: ProductImage,
    variant400: _PreparedVariant,
    variant800: _PreparedVariant | None,
) -> bool:
    if not row.has_fresh_thumbs or variant400.object_key != row.thumb_400_key:
        return False
    if variant800 is None:
        return row.thumb_800_key == row.thumb_400_key
    return variant800.object_key == row.thumb_800_key


def _build_variants(
    *,
    source_key: str,
    img: Image.Image,
    use_versioned: bool,
) -> tuple[_PreparedVariant, _PreparedVariant | None, set[str]]:
    w400_target = min(400, img.width)
    w800_target = min(800, img.width)

    img400, display400 = _resize_copy(img, w400_target)
    content400 = _encode_webp(img400)
    key400 = _choose_object_key(source_key=source_key, width=400, content=content400, use_versioned=use_versioned)
    variant400 = _PreparedVariant(display_width=display400, content=content400, object_key=key400)

    if w800_target == display400 and w400_target == w800_target:
        return variant400, None, {key400}

    img800, display800 = _resize_copy(img, w800_target)
    content800 = _encode_webp(img800)
    if content800 == content400 and display800 == display400:
        return variant400, None, {key400}

    key800 = _choose_object_key(source_key=source_key, width=800, content=content800, use_versioned=use_versioned)
    if key800 == key400:
        return variant400, None, {key400}

    variant800 = _PreparedVariant(display_width=display800, content=content800, object_key=key800)
    return variant400, variant800, {key400, key800}


def generate_product_image_thumbnails(  # noqa: C901, PLR0911, PLR0912, PLR0914, PLR0915
    product_image_id: int,
    *,
    force: bool = False,
) -> ThumbnailResult:
    started_at = time.monotonic()
    row = ProductImage.objects.filter(pk=product_image_id).first()
    if row is None:
        return "missing_source"

    source_key = row.image.name if row.image else ""
    if not source_key:
        log_event(logger, logging.INFO, "product_image.thumbnails.missing_source", product_image_id=product_image_id)
        return "missing_source"

    if row.has_fresh_thumbs and not force:
        log_event(
            logger,
            logging.INFO,
            "product_image.thumbnails.skipped",
            product_image_id=product_image_id,
            reason="fresh",
        )
        return "skipped"

    log_event(
        logger,
        logging.INFO,
        "product_image.thumbnails.started",
        product_image_id=product_image_id,
        source_key=source_key,
        force=force,
    )

    try:
        source_bytes = read_product_image_source_bytes(source_key)
    except ProductImageMigrationMissingSource:
        log_event(
            logger,
            logging.INFO,
            "product_image.thumbnails.missing_source",
            product_image_id=product_image_id,
            source_key=source_key,
        )
        return "missing_source"
    except ProductImageThumbnailTransientError:
        raise

    try:
        img, orig_width, orig_height = _prepare_image(source_bytes)
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        log_event(
            logger,
            logging.WARNING,
            "product_image.thumbnails.failed",
            product_image_id=product_image_id,
            source_key=source_key,
            error_type=type(exc).__name__,
        )
        return "failed"

    use_versioned = force or row.has_fresh_thumbs
    old_keys = {row.thumb_400_key, row.thumb_800_key} - {""}

    try:
        variant400, variant800, planned_keys = _build_variants(
            source_key=source_key,
            img=img,
            use_versioned=use_versioned,
        )
    except (ClientError, BotoCoreError) as exc:
        if isinstance(exc, ClientError) and not _is_transient_client_error(exc):
            log_event(
                logger,
                logging.WARNING,
                "product_image.thumbnails.failed",
                product_image_id=product_image_id,
                error_type=type(exc).__name__,
            )
            return "failed"
        raise ProductImageThumbnailTransientError(str(exc)) from exc

    if (
        not force
        and row.has_fresh_thumbs
        and variant400.object_key in old_keys
        and (variant800 is None or variant800.object_key in old_keys)
    ):
        return "skipped"

    if force and _planned_keys_match_row(row, variant400, variant800):
        return "skipped"

    uploaded_keys: set[str] = set()
    try:
        _upload_webp(variant400.object_key, variant400.content)
        uploaded_keys.add(variant400.object_key)
        key800 = variant400.object_key
        display800 = variant400.display_width
        if variant800 is not None:
            _upload_webp(variant800.object_key, variant800.content)
            uploaded_keys.add(variant800.object_key)
            key800 = variant800.object_key
            display800 = variant800.display_width
    except (ClientError, BotoCoreError, OSError) as exc:
        if isinstance(exc, ClientError) and _is_transient_client_error(exc):
            _schedule_orphan_cleanup(uploaded_keys)
            raise ProductImageThumbnailTransientError(str(exc)) from exc
        _schedule_orphan_cleanup(uploaded_keys)
        log_event(
            logger,
            logging.WARNING,
            "product_image.thumbnails.failed",
            product_image_id=product_image_id,
            error_type=type(exc).__name__,
        )
        return "failed"

    thumb_400_key = variant400.object_key
    thumb_800_key = key800
    thumb_400_display_width = variant400.display_width
    thumb_800_display_width = display800

    publish_result, keys_to_retire = _publish_thumbnails(
        product_image_id=product_image_id,
        source_key=source_key,
        force=force,
        thumb_400_key=thumb_400_key,
        thumb_800_key=thumb_800_key,
        thumb_400_display_width=thumb_400_display_width,
        thumb_800_display_width=thumb_800_display_width,
        orig_width=orig_width,
        orig_height=orig_height,
        uploaded_keys=uploaded_keys,
    )
    if publish_result == "skipped":
        return "skipped"

    delay = getattr(settings, "PRODUCT_IMAGE_THUMB_STALE_DELETE_DELAY_SECONDS", 86400)
    for stale_key in keys_to_retire:
        schedule_product_image_delayed_delete_on_commit(
            object_key=stale_key,
            product_id=row.product_id,
            product_image_id=product_image_id,
            reason=DELETE_REASON_THUMB_REGENERATE,
            delay_seconds=delay,
        )

    duration_ms = int((time.monotonic() - started_at) * 1000)
    log_event(
        logger,
        logging.INFO,
        "product_image.thumbnails.generated",
        product_image_id=product_image_id,
        source_key=source_key,
        thumb_400_key=thumb_400_key,
        thumb_800_key=thumb_800_key,
        source_bytes=len(source_bytes),
        duration_ms=duration_ms,
    )
    return "generated"


def _schedule_orphan_cleanup(keys: set[str]) -> None:
    for key in keys:
        schedule_product_image_delete_on_commit(
            object_key=key,
            product_id=None,
            product_image_id=None,
            reason="orphan_thumbnail",
        )


def _publish_thumbnails(  # noqa: PLR0913
    *,
    product_image_id: int,
    source_key: str,
    force: bool,
    thumb_400_key: str,
    thumb_800_key: str,
    thumb_400_display_width: int,
    thumb_800_display_width: int,
    orig_width: int,
    orig_height: int,
    uploaded_keys: set[str],
) -> tuple[Literal["published", "skipped"], set[str]]:
    keys_to_retire: set[str] = set()
    with transaction.atomic():
        locked = ProductImage.objects.select_for_update().filter(pk=product_image_id).first()
        if locked is None or (locked.image.name or "") != source_key:
            _schedule_orphan_cleanup(uploaded_keys)
            return "skipped", set()

        if locked.has_fresh_thumbs and not force:
            _schedule_orphan_cleanup(uploaded_keys - {locked.thumb_400_key, locked.thumb_800_key})
            return "skipped", set()

        if (
            force
            and locked.has_fresh_thumbs
            and locked.thumb_400_key == thumb_400_key
            and locked.thumb_800_key == thumb_800_key
        ):
            return "skipped", set()

        if force or locked.has_fresh_thumbs:
            keys_to_retire = {locked.thumb_400_key, locked.thumb_800_key} - {""} - {thumb_400_key, thumb_800_key}

        updated = ProductImage.objects.filter(pk=product_image_id, image=source_key).update(
            thumb_400_key=thumb_400_key,
            thumb_800_key=thumb_800_key,
            thumb_400_display_width=thumb_400_display_width,
            thumb_800_display_width=thumb_800_display_width,
            thumbs_source_key=source_key,
            width=orig_width,
            height=orig_height,
            updated_at=timezone.now(),
        )
        if updated == 0:
            _schedule_orphan_cleanup(uploaded_keys)
            return "skipped", set()

    return "published", keys_to_retire


def public_url_for_key(object_key: str) -> str:
    if settings.S3_PRODUCT_IMAGES_ENABLED:
        return build_product_image_public_url(object_key)
    return get_product_image_storage().url(object_key)
