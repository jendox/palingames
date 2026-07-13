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

from apps.products.models import Product, ProductFile
from apps.products.services.s3 import (
    ProductFileMetadataError,
    ProductFileUploadUrlError,
    build_custom_game_file_key,
    build_product_file_key,
    delete_product_file,
    generate_presigned_upload_url,
    validate_upload_filename,
    verify_uploaded_object,
)
from apps.products.upload_intents import (
    CustomGameUploadIntent,
    UploadIntent,
    consume_custom_game_upload_intent,
    consume_upload_intent,
    create_custom_game_upload_intent,
    create_upload_intent,
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


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValidationError("Поле is_active должно быть булевым значением.")


def _parse_required_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Поле {field_name} обязательно.")
    return value.strip()


def _validate_size_bytes(size_bytes: int) -> None:
    if size_bytes > settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"Размер файла не должен превышать {settings.ADMIN_DIRECT_S3_UPLOAD_MAX_BYTES / 1048576}MB.",
        )


@dataclass(frozen=True)
class PresignRequest:
    product: Product
    safe_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class FinalizeRequest:
    intent_id: str
    product_id: int
    file_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_active: bool
    product_file_id: int | None


def _parse_presign_request(data: dict[str, Any]) -> PresignRequest:
    product_id = _parse_positive_int(data.get("product_id"), field_name="product_id")
    size_bytes = _parse_positive_int(data.get("size_bytes"), field_name="size_bytes")
    _validate_size_bytes(size_bytes)

    safe_name = validate_upload_filename(_parse_required_str(data.get("filename"), field_name="filename"))
    content_type = data.get("content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        content_type = _guess_content_type(safe_name)
    else:
        content_type = content_type.strip()

    product = Product.objects.get(pk=product_id)
    return PresignRequest(
        product=product,
        safe_name=safe_name,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _parse_finalize_request(data: dict[str, Any]) -> FinalizeRequest:
    product_file_id = data.get("product_file_id")
    if product_file_id is not None:
        product_file_id = _parse_positive_int(product_file_id, field_name="product_file_id")

    return FinalizeRequest(
        intent_id=_parse_required_str(data.get("intent_id"), field_name="intent_id"),
        product_id=_parse_positive_int(data.get("product_id"), field_name="product_id"),
        file_key=_parse_required_str(data.get("file_key"), field_name="file_key"),
        original_filename=_parse_required_str(data.get("original_filename"), field_name="original_filename"),
        mime_type=_parse_required_str(data.get("mime_type"), field_name="mime_type"),
        size_bytes=_parse_positive_int(data.get("size_bytes"), field_name="size_bytes"),
        is_active=_parse_bool(data.get("is_active"), default=True),
        product_file_id=product_file_id,
    )


def _validate_intent_bindings(intent: UploadIntent, request_data: FinalizeRequest) -> None:
    if intent.product_id != request_data.product_id:
        raise ValidationError("product_id не совпадает с upload intent.")
    if intent.file_key != request_data.file_key:
        raise ValidationError("file_key не совпадает с upload intent.")
    if intent.size_bytes != request_data.size_bytes:
        raise ValidationError("size_bytes не совпадает с upload intent.")
    if intent.content_type.strip().lower() != request_data.mime_type.strip().lower():
        raise ValidationError("mime_type не совпадает с upload intent.")


def _upsert_product_file_from_intent(
    *,
    intent: UploadIntent,
    request_data: FinalizeRequest,
) -> tuple[ProductFile, str | None]:
    Product.objects.get(pk=request_data.product_id)
    safe_name = validate_upload_filename(request_data.original_filename)

    previous_file_key: str | None = None
    if request_data.product_file_id is not None:
        obj = ProductFile.objects.select_for_update().get(
            pk=request_data.product_file_id,
            product_id=request_data.product_id,
        )
        previous_file_key = obj.file_key
    else:
        obj = ProductFile(product_id=request_data.product_id)

    obj.file_key = intent.file_key
    obj.original_filename = safe_name
    obj.mime_type = intent.content_type
    obj.size_bytes = intent.size_bytes
    obj.checksum_sha256 = None
    obj.is_active = request_data.is_active

    if request_data.is_active:
        ProductFile.objects.filter(product_id=request_data.product_id, is_active=True).exclude(pk=obj.pk).update(
            is_active=False,
        )

    obj.save()
    return obj, previous_file_key


@staff_json_required
@require_POST
def product_file_presign(request: HttpRequest) -> JsonResponse:
    if not settings.ADMIN_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        presign_request = _parse_presign_request(_parse_json(request))
        file_key = build_product_file_key(
            product_slug=presign_request.product.slug,
            filename=presign_request.safe_name,
        )
        intent_id = create_upload_intent(
            user_id=request.user.id,
            product_id=presign_request.product.id,
            file_key=file_key,
            size_bytes=presign_request.size_bytes,
            content_type=presign_request.content_type,
        )
        presign = generate_presigned_upload_url(file_key=file_key, content_type=presign_request.content_type)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Продукт не найден."}, status=400)
    except ProductFileUploadUrlError:
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
def product_file_finalize(request: HttpRequest) -> JsonResponse:
    if not settings.ADMIN_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        finalize_request = _parse_finalize_request(_parse_json(request))
        intent = consume_upload_intent(intent_id=finalize_request.intent_id, user_id=request.user.id)
        _validate_intent_bindings(intent, finalize_request)
        verify_uploaded_object(
            file_key=intent.file_key,
            expected_size=intent.size_bytes,
            content_type=intent.content_type,
        )
        obj, previous_file_key = _upsert_product_file_from_intent(
            intent=intent,
            request_data=finalize_request,
        )
        if previous_file_key and previous_file_key != obj.file_key:
            delete_product_file(file_key=previous_file_key)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Продукт не найден."}, status=400)
    except ProductFile.DoesNotExist:
        return JsonResponse({"error": "Файл продукта не найден."}, status=400)
    except ProductFileMetadataError:
        return JsonResponse({"error": "uploaded_file_not_found"}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "product_file_id": obj.pk,
            "redirect_url": reverse("admin:products_productfile_change", args=[obj.pk]),
        },
    )


@dataclass(frozen=True)
class CustomGamePresignRequest:
    custom_game_request: Any
    safe_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class CustomGameFinalizeRequest:
    intent_id: str
    request_id: int
    file_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_active: bool
    custom_game_file_id: int | None


def _parse_custom_game_presign_request(data: dict[str, Any]) -> CustomGamePresignRequest:
    from apps.custom_games.models import CustomGameRequest

    request_id = _parse_positive_int(data.get("request_id"), field_name="request_id")
    size_bytes = _parse_positive_int(data.get("size_bytes"), field_name="size_bytes")
    _validate_size_bytes(size_bytes)

    safe_name = validate_upload_filename(_parse_required_str(data.get("filename"), field_name="filename"))
    content_type = data.get("content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        content_type = _guess_content_type(safe_name)
    else:
        content_type = content_type.strip()

    custom_game_request = CustomGameRequest.objects.get(pk=request_id)
    return CustomGamePresignRequest(
        custom_game_request=custom_game_request,
        safe_name=safe_name,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _parse_custom_game_finalize_request(data: dict[str, Any]) -> CustomGameFinalizeRequest:
    custom_game_file_id = data.get("custom_game_file_id")
    if custom_game_file_id is not None:
        custom_game_file_id = _parse_positive_int(custom_game_file_id, field_name="custom_game_file_id")

    return CustomGameFinalizeRequest(
        intent_id=_parse_required_str(data.get("intent_id"), field_name="intent_id"),
        request_id=_parse_positive_int(data.get("request_id"), field_name="request_id"),
        file_key=_parse_required_str(data.get("file_key"), field_name="file_key"),
        original_filename=_parse_required_str(data.get("original_filename"), field_name="original_filename"),
        mime_type=_parse_required_str(data.get("mime_type"), field_name="mime_type"),
        size_bytes=_parse_positive_int(data.get("size_bytes"), field_name="size_bytes"),
        is_active=_parse_bool(data.get("is_active"), default=True),
        custom_game_file_id=custom_game_file_id,
    )


def _validate_custom_game_intent_bindings(
    intent: CustomGameUploadIntent,
    request_data: CustomGameFinalizeRequest,
) -> None:
    if intent.request_id != request_data.request_id:
        raise ValidationError("request_id не совпадает с upload intent.")
    if intent.file_key != request_data.file_key:
        raise ValidationError("file_key не совпадает с upload intent.")
    if intent.size_bytes != request_data.size_bytes:
        raise ValidationError("size_bytes не совпадает с upload intent.")
    if intent.content_type.strip().lower() != request_data.mime_type.strip().lower():
        raise ValidationError("mime_type не совпадает с upload intent.")


def _upsert_custom_game_file_from_intent(
    *,
    intent: CustomGameUploadIntent,
    request_data: CustomGameFinalizeRequest,
    uploaded_by_id: int,
) -> tuple[Any, str | None]:
    from apps.custom_games.models import CustomGameFile, CustomGameRequest

    CustomGameRequest.objects.get(pk=request_data.request_id)
    safe_name = validate_upload_filename(request_data.original_filename)

    previous_file_key: str | None = None
    if request_data.custom_game_file_id is not None:
        obj = CustomGameFile.objects.select_for_update().get(
            pk=request_data.custom_game_file_id,
            request_id=request_data.request_id,
        )
        previous_file_key = obj.file_key
    else:
        obj = CustomGameFile(request_id=request_data.request_id)

    obj.file_key = intent.file_key
    obj.original_filename = safe_name
    obj.mime_type = intent.content_type
    obj.size_bytes = intent.size_bytes
    obj.checksum_sha256 = None
    obj.is_active = request_data.is_active
    if not obj.uploaded_by_id:
        obj.uploaded_by_id = uploaded_by_id

    if request_data.is_active:
        CustomGameFile.objects.filter(request_id=request_data.request_id, is_active=True).exclude(pk=obj.pk).update(
            is_active=False,
        )

    obj.save()
    return obj, previous_file_key


@staff_json_required
@require_POST
def custom_game_file_presign(request: HttpRequest) -> JsonResponse:
    from apps.custom_games.models import CustomGameRequest

    if not settings.ADMIN_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        presign_request = _parse_custom_game_presign_request(_parse_json(request))
        payment_account_no = presign_request.custom_game_request.payment_account_no
        if not payment_account_no:
            raise ValidationError("У заказа отсутствует payment_account_no.")
        file_key = build_custom_game_file_key(
            payment_account_no=payment_account_no,
            filename=presign_request.safe_name,
        )
        intent_id = create_custom_game_upload_intent(
            user_id=request.user.id,
            request_id=presign_request.custom_game_request.id,
            file_key=file_key,
            size_bytes=presign_request.size_bytes,
            content_type=presign_request.content_type,
        )
        presign = generate_presigned_upload_url(file_key=file_key, content_type=presign_request.content_type)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CustomGameRequest.DoesNotExist:
        return JsonResponse({"error": "Заказ не найден."}, status=400)
    except ProductFileUploadUrlError:
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
def custom_game_file_finalize(request: HttpRequest) -> JsonResponse:
    from apps.custom_games.models import CustomGameFile, CustomGameRequest

    if not settings.ADMIN_DIRECT_S3_UPLOAD_ENABLED:
        return _feature_disabled_response()

    try:
        finalize_request = _parse_custom_game_finalize_request(_parse_json(request))
        intent = consume_custom_game_upload_intent(intent_id=finalize_request.intent_id, user_id=request.user.id)
        _validate_custom_game_intent_bindings(intent, finalize_request)
        verify_uploaded_object(
            file_key=intent.file_key,
            expected_size=intent.size_bytes,
            content_type=intent.content_type,
        )
        obj, previous_file_key = _upsert_custom_game_file_from_intent(
            intent=intent,
            request_data=finalize_request,
            uploaded_by_id=request.user.id,
        )
        if previous_file_key and previous_file_key != obj.file_key:
            delete_product_file(file_key=previous_file_key)
    except ValidationError as exc:
        return _validation_error_response(exc)
    except CustomGameRequest.DoesNotExist:
        return JsonResponse({"error": "Заказ не найден."}, status=400)
    except CustomGameFile.DoesNotExist:
        return JsonResponse({"error": "Файл заказа не найден."}, status=400)
    except ProductFileMetadataError:
        return JsonResponse({"error": "uploaded_file_not_found"}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "custom_game_file_id": obj.pk,
            "redirect_url": reverse("admin:custom_games_customgamefile_change", args=[obj.pk]),
        },
    )
