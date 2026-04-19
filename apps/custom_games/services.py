from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.core.logging import log_event
from apps.custom_games.emails import (
    send_custom_game_request_admin_email,
    send_custom_game_request_customer_email,
)
from apps.custom_games.models import CustomGameDownloadToken, CustomGameRequest
from apps.notifications.services import enqueue_notification
from apps.notifications.types import CUSTOM_GAME_DOWNLOAD

logger = logging.getLogger("apps.custom_games")
CUSTOM_GAME_DOWNLOAD_TOKEN_BYTES = 32
CUSTOM_GAME_DOWNLOAD_TOKEN_PREFIX_LENGTH = 12


def create_custom_game_request(*, form, user=None) -> CustomGameRequest:
    custom_game_request = form.save(commit=False)
    if user is not None and user.is_authenticated:
        custom_game_request.user = user

    with transaction.atomic():
        custom_game_request.save()

    notify_custom_game_request_created(custom_game_request)
    return custom_game_request


def notify_custom_game_request_created(custom_game_request: CustomGameRequest) -> None:
    try:
        send_custom_game_request_customer_email(custom_game_request=custom_game_request)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "custom_game_request.customer_email.failed",
            exc_info=True,
            custom_game_request_id=custom_game_request.id,
            payment_account_no=custom_game_request.payment_account_no,
        )

    try:
        send_custom_game_request_admin_email(custom_game_request=custom_game_request)
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "custom_game_request.admin_email.failed",
            exc_info=True,
            custom_game_request_id=custom_game_request.id,
            payment_account_no=custom_game_request.payment_account_no,
        )


def hash_custom_game_download_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _download_token_expiration_delta() -> timedelta:
    return timedelta(hours=settings.GUEST_ACCESS_EXPIRE_HOURS)


def create_custom_game_download_token(custom_game_request: CustomGameRequest) -> tuple[CustomGameDownloadToken, str]:
    raw_token = secrets.token_urlsafe(CUSTOM_GAME_DOWNLOAD_TOKEN_BYTES)
    token_hash = hash_custom_game_download_token(raw_token)
    download_token = CustomGameDownloadToken.objects.create(
        request=custom_game_request,
        token_hash=token_hash,
        token_prefix=raw_token[:CUSTOM_GAME_DOWNLOAD_TOKEN_PREFIX_LENGTH],
        email=custom_game_request.contact_email,
        expires_at=timezone.now() + _download_token_expiration_delta(),
        max_downloads=settings.GUEST_ACCESS_MAX_DOWNLOADS,
    )
    return download_token, raw_token


def send_custom_game_download_link(*, custom_game_request: CustomGameRequest) -> CustomGameDownloadToken:
    active_file = custom_game_request.files.filter(is_active=True).first()
    if active_file is None:
        raise ValueError("Custom game request must have an active file before sending a download link")

    download_token, raw_token = create_custom_game_download_token(custom_game_request)
    outbox = enqueue_notification(
        notification_type=CUSTOM_GAME_DOWNLOAD,
        recipient=custom_game_request.contact_email,
        payload={
            "custom_game_request_id": custom_game_request.id,
            "download_token_id": download_token.id,
            "raw_token": raw_token,
        },
        target=custom_game_request,
    )
    log_event(
        logger,
        logging.INFO,
        "custom_game_request.download_email.queued",
        custom_game_request_id=custom_game_request.id,
        payment_account_no=custom_game_request.payment_account_no,
        download_token_id=download_token.id,
        notification_outbox_id=outbox.id,
        email=custom_game_request.contact_email,
    )
    return download_token


def resolve_custom_game_download_token(raw_token: str) -> CustomGameDownloadToken | None:
    token_hash = hash_custom_game_download_token(raw_token)
    download_token = (
        CustomGameDownloadToken.objects.select_related("request")
        .filter(
            token_hash=token_hash,
            expires_at__gt=timezone.now(),
            downloads_count__lt=F("max_downloads"),
        )
        .first()
    )
    return download_token


def mark_custom_game_download_token_used(download_token: CustomGameDownloadToken) -> bool:
    updated = CustomGameDownloadToken.objects.filter(
        pk=download_token.pk,
        expires_at__gt=timezone.now(),
        downloads_count__lt=F("max_downloads"),
    ).update(
        downloads_count=F("downloads_count") + 1,
        last_downloaded_at=timezone.now(),
        updated_at=timezone.now(),
    )
    if not updated:
        return False
    download_token.refresh_from_db(fields=["downloads_count", "last_downloaded_at", "updated_at"])
    return True


def release_custom_game_download_token_use(download_token: CustomGameDownloadToken) -> None:
    updated = CustomGameDownloadToken.objects.filter(
        pk=download_token.pk,
        downloads_count__gt=0,
    ).update(
        downloads_count=F("downloads_count") - 1,
        updated_at=timezone.now(),
    )
    if updated:
        download_token.refresh_from_db(fields=["downloads_count", "updated_at"])
