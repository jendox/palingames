import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class ManagedLinkUploadIntent:
    intent_id: str
    user_id: int
    managed_link_id: int
    file_key: str
    size_bytes: int
    content_type: str


INTENT_KEY_PREFIX = "admin-managed-link-upload-intent"


def intent_key(intent_id: str) -> str:
    return f"{INTENT_KEY_PREFIX}:{intent_id}"


def create_managed_link_upload_intent(
    *,
    user_id: int,
    managed_link_id: int,
    file_key: str,
    size_bytes: int,
    content_type: str,
) -> str:
    if user_id <= 0 or managed_link_id <= 0:
        raise ValidationError("Недопустимые идентификаторы пользователя или ссылки.")

    if size_bytes <= 0 or size_bytes > settings.MANAGED_LINK_UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"Размер файла не должен превышать {settings.MANAGED_LINK_UPLOAD_MAX_BYTES / 1048576}MB.",
        )

    if not file_key.strip():
        raise ValidationError("Недопустимый ключ файла.")

    intent_id = uuid.uuid4().hex
    cache.set(
        key=intent_key(intent_id),
        value={
            "user_id": user_id,
            "managed_link_id": managed_link_id,
            "file_key": file_key,
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
        timeout=settings.MANAGED_LINK_UPLOAD_PRESIGN_TTL_SECONDS,
    )
    return intent_id


def consume_managed_link_upload_intent(*, intent_id: str, user_id: int) -> ManagedLinkUploadIntent:
    if not intent_id:
        raise ValidationError("Недействительный upload intent.")

    key = intent_key(intent_id)
    payload = cache.get(key=key)
    if not payload:
        raise ValidationError("Upload intent истёк или не найден.")

    if payload["user_id"] != user_id:
        raise ValidationError("Upload intent принадлежит другому пользователю.")

    cache.delete(key=key)
    return ManagedLinkUploadIntent(
        intent_id=intent_id,
        **payload,
    )
