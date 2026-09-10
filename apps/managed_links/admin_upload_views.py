from __future__ import annotations

import json
import mimetypes
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.managed_links.models import ManagedLink
from apps.managed_links.services.storage import (
    ManagedLinkMetadataError,
    ManagedLinkUploadUrlError,
    build_managed_link_file_key,
    delete_managed_link_file,
    generate_presigned_upload_url,
    validate_upload_filename,
    verify_uploaded_object,
)
from apps.managed_links.upload_intents import (
    consume_managed_link_upload_intent,
    create_managed_link_upload_intent,
)


def staff_json_required(view: Callable[..., JsonResponse]) -> Callable[..., JsonResponse]:
    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        if not request.user.is_authenticated or not request.user.is_staff:
            return JsonResponse({"error": "staff_required"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


def _feature_disabled_response() -> JsonResponse:
    return JsonResponse({"error": "feature_disabled"}, status=404)


def _parse_json(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("Некорректный JSON.") from exc
    if not isinstance(data, dict):
        raise ValidationError("Ожидается JSON-объект.")
    return data


def _validation_error_response(exc: ValidationError) -> JsonResponse:
    if hasattr(exc, "message_dict"):
        return JsonResponse({"error": exc.message_dict}, status=400)
    messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
    return JsonResponse({"error": messages[0]}, status=400)


def _guess_content_type(filename: str) -> str:
    guessed_type, _ = mimetypes.guess_type(filename)
    return guessed_type or "application/octet-stream"


def _parse_positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Поле {field_name} должно быть целым числом.") from exc
    if parsed <= 0:
        raise ValidationError(f"Поле {field_name} должно быть больше 0.")
    return parsed


def _parse_required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Поле {field_name} обязательно.")
    return value.strip()


def _validate_size_bytes(size_bytes: int) -> None:
    if size_bytes > settings.MANAGED_LINK_UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"Размер файла не должен превышать {settings.MANAGED_LINK_UPLOAD_MAX_BYTES / 1048576}MB.",
        )


@dataclass(frozen=True)
class PresignRequest:
    managed_link: ManagedLink
    safe_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class FinalizeRequest:
    intent_id: str
    managed_link_id: int
    file_key: str
    original_filename: str
    mime_type: str
    size_bytes: int


def _parse_presign_request(data: dict[str, Any]) -> PresignRequest:
    managed_link_id = _parse_positive_int(data.get("managed_link_id"), field_name="managed_link_id")
    size_bytes = _parse_positive_int(data.get("size_bytes"), field_name="size_bytes")
    _validate_size_bytes(size_bytes)

    safe_name = validate_upload_filename(_parse_required_str(data.get("filename"), field_name="filename"))
    content_type = data.get("content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        content_type = _guess_content_type(safe_name)
    else:
        content_type = content_type.strip()

    managed_link = ManagedLink.objects.get(pk=managed_link_id)
    return PresignRequest(
        managed_link=managed_link,
        safe_name=safe_name,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _parse_finalize_request(data: dict[str, Any]) -> FinalizeRequest:
    return FinalizeRequest(
        intent_id=_parse_required_str(data.get("intent_id"), field_name="intent_id"),
        managed_link_id=_parse_positive_int(data.get("managed_link_id"), field_name="managed_link_id"),
        file_key=_parse_required_str(data.get("file_key"), field_name="file_key"),
        original_filename=_parse_required_str(data.get("original_filename"), field_name="original_filename"),
        mime_type=_parse_required_str(data.get("mime_type"), field_name="mime_type"),
        size_bytes=_parse_positive_int(data.get("size_bytes"), field_name="size_bytes"),
    )


def _validate_intent_bindings(intent, request_data: FinalizeRequest) -> None:
    if intent.managed_link_id != request_data.managed_link_id:
        raise ValidationError("managed_link_id не совпадает с upload intent.")
    if intent.file_key != request_data.file_key:
        raise ValidationError("file_key не совпадает с upload intent.")
    if intent.size_bytes != request_data.size_bytes:
        raise ValidationError("size_bytes не совпадает с upload intent.")
    if intent.content_type.strip().lower() != request_data.mime_type.strip().lower():
        raise ValidationError("mime_type не совпадает с upload intent.")


@staff_json_required
@require_POST
def managed_link_presign(request: HttpRequest) -> JsonResponse:
    if not settings.MANAGED_LINK_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        presign_request = _parse_presign_request(_parse_json(request))
        file_key = build_managed_link_file_key(
            token=presign_request.managed_link.token,
            filename=presign_request.safe_name,
        )
        intent_id = create_managed_link_upload_intent(
            user_id=request.user.id,
            managed_link_id=presign_request.managed_link.id,
            file_key=file_key,
            size_bytes=presign_request.size_bytes,
            content_type=presign_request.content_type,
        )
        presign = generate_presigned_upload_url(
            file_key=file_key,
            content_type=presign_request.content_type,
        )
    except ValidationError as exc:
        return _validation_error_response(exc)
    except ManagedLink.DoesNotExist:
        return JsonResponse({"error": "Управляемая ссылка не найдена."}, status=400)
    except ManagedLinkUploadUrlError:
        return JsonResponse({"error": "storage_unavailable"}, status=503)

    return JsonResponse(
        {
            "intent_id": intent_id,
            "file_key": file_key,
            "upload_url": presign["upload_url"],
            "required_headers": presign["required_headers"],
            "expires_in": presign["expires_in"],
        },
    )


@staff_json_required
@require_POST
@transaction.atomic
def managed_link_finalize(request: HttpRequest) -> JsonResponse:
    if not settings.MANAGED_LINK_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        finalize_request = _parse_finalize_request(_parse_json(request))
        intent = consume_managed_link_upload_intent(intent_id=finalize_request.intent_id, user_id=request.user.id)
        _validate_intent_bindings(intent, finalize_request)
        verify_uploaded_object(
            file_key=intent.file_key,
            expected_size=intent.size_bytes,
            content_type=intent.content_type,
        )
        managed_link = ManagedLink.objects.select_for_update().get(pk=finalize_request.managed_link_id)
        previous_file_key = managed_link.s3_file_key or None
        safe_name = validate_upload_filename(finalize_request.original_filename)

        managed_link.s3_file_key = intent.file_key
        managed_link.original_filename = safe_name
        managed_link.mime_type = intent.content_type
        managed_link.size_bytes = intent.size_bytes
        managed_link.checksum_sha256 = None
        managed_link.is_active = True
        managed_link.full_clean()
        managed_link.save()

        if previous_file_key and previous_file_key != managed_link.s3_file_key:
            delete_managed_link_file(file_key=previous_file_key)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except ManagedLink.DoesNotExist:
        return JsonResponse({"error": "Управляемая ссылка не найдена."}, status=400)
    except ManagedLinkMetadataError:
        return JsonResponse({"error": "uploaded_file_not_found"}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "managed_link_id": managed_link.pk,
            "redirect_url": reverse("admin:managed_links_managedlink_change", args=[managed_link.pk]),
        },
    )
