import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class UploadIntent:
    intent_id: str
    user_id: int
    product_id: int
    file_key: str
    size_bytes: int
    content_type: str


@dataclass(frozen=True)
class CustomGameUploadIntent:
    intent_id: str
    user_id: int
    request_id: int
    file_key: str
    size_bytes: int
    content_type: str


CACHE_ALIAS = "default"
INTENT_KEY_PREFIX = "admin-upload-intent"
CUSTOM_GAME_INTENT_KEY_PREFIX = "admin-custom-game-upload-intent"


def intent_key(intent_id: str) -> str:
    return f"{INTENT_KEY_PREFIX}:{intent_id}"


def custom_game_intent_key(intent_id: str) -> str:
    return f"{CUSTOM_GAME_INTENT_KEY_PREFIX}:{intent_id}"


def create_upload_intent(
    *,
    user_id: int,
    product_id: int,
    file_key: str,
    size_bytes: int,
    content_type: str,
) -> str:
    if user_id <= 0 or product_id <= 0:
        raise ValidationError("Недопустимые идентификаторы пользователя или продукта.")

    if size_bytes <= 0 or size_bytes > settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"Размер файла не должен превышать {settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES / 1048576}MB.",
        )

    if (
        not file_key.strip()
        or file_key.startswith(f"{settings.S3_PRODUCT_IMAGES_PREFIX}/")
        or file_key == settings.S3_PRODUCT_IMAGES_PREFIX
    ):
        raise ValidationError("Недопустимый ключ файла.")

    intent_id = uuid.uuid4().hex
    cache.set(
        key=intent_key(intent_id),
        value={
            "user_id": user_id,
            "product_id": product_id,
            "file_key": file_key,
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
        timeout=settings.ADMIN_DIRECT_S3_UPLOAD_PRESIGN_TTL_SECONDS,
    )

    return intent_id


def get_upload_intent(*, intent_id: str) -> UploadIntent | None:
    if not intent_id:
        raise ValidationError("Недействительный upload intent.")

    key = intent_key(intent_id)
    payload = cache.get(key=key)

    return UploadIntent(
        intent_id=intent_id,
        **payload,
    ) if payload else None


def consume_upload_intent(*, intent_id: str, user_id: int) -> UploadIntent:
    if not intent_id:
        raise ValidationError("Недействительный upload intent.")

    key = intent_key(intent_id)

    payload = cache.get(key=key)
    if not payload:
        raise ValidationError("Upload intent истёк или не найден.")

    if payload["user_id"] != user_id:
        raise ValidationError("Upload intent принадлежит другому пользователю.")

    cache.delete(key=key)

    return UploadIntent(
        intent_id=intent_id,
        **payload,
    )


def create_custom_game_upload_intent(
    *,
    user_id: int,
    request_id: int,
    file_key: str,
    size_bytes: int,
    content_type: str,
) -> str:
    if user_id <= 0 or request_id <= 0:
        raise ValidationError("Недопустимые идентификаторы пользователя или заказа.")

    if size_bytes <= 0 or size_bytes > settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"Размер файла не должен превышать {settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES / 1048576}MB.",
        )

    if (
        not file_key.strip()
        or file_key.startswith(f"{settings.S3_PRODUCT_IMAGES_PREFIX}/")
        or file_key == settings.S3_PRODUCT_IMAGES_PREFIX
    ):
        raise ValidationError("Недопустимый ключ файла.")

    intent_id = uuid.uuid4().hex
    cache.set(
        key=custom_game_intent_key(intent_id),
        value={
            "user_id": user_id,
            "request_id": request_id,
            "file_key": file_key,
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
        timeout=settings.ADMIN_DIRECT_S3_UPLOAD_PRESIGN_TTL_SECONDS,
    )

    return intent_id


def consume_custom_game_upload_intent(*, intent_id: str, user_id: int) -> CustomGameUploadIntent:
    if not intent_id:
        raise ValidationError("Недействительный upload intent.")

    key = custom_game_intent_key(intent_id)

    payload = cache.get(key=key)
    if not payload:
        raise ValidationError("Upload intent истёк или не найден.")

    if payload["user_id"] != user_id:
        raise ValidationError("Upload intent принадлежит другому пользователю.")

    cache.delete(key=key)

    return CustomGameUploadIntent(
        intent_id=intent_id,
        **payload,
    )
