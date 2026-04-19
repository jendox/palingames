from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.access.emails import build_absolute_url
from apps.core.logging import log_event

logger = logging.getLogger("apps.custom_games.email")


def send_custom_game_request_customer_email(*, custom_game_request) -> None:
    subject = f"Заявка на игру {custom_game_request.payment_account_no} получена"
    context = _build_email_context(custom_game_request)
    text_body = render_to_string("custom_games/email/request_customer_message.txt", context)
    html_body = render_to_string("custom_games/email/request_customer_message.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[custom_game_request.contact_email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()
    log_event(
        logger,
        logging.INFO,
        "custom_game_request.customer_email.sent",
        custom_game_request_id=custom_game_request.id,
        payment_account_no=custom_game_request.payment_account_no,
        email=custom_game_request.contact_email,
    )


def send_custom_game_request_admin_email(*, custom_game_request) -> None:
    recipients = list(settings.CUSTOM_GAME_ADMIN_EMAILS)
    if not recipients:
        log_event(
            logger,
            logging.WARNING,
            "custom_game_request.admin_email.skipped",
            custom_game_request_id=custom_game_request.id,
            reason="empty_recipients",
        )
        return

    subject = f"Новая заявка на игру {custom_game_request.payment_account_no}"
    context = _build_email_context(custom_game_request)
    text_body = render_to_string("custom_games/email/request_admin_message.txt", context)
    html_body = render_to_string("custom_games/email/request_admin_message.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    message.attach_alternative(html_body, "text/html")
    message.send()
    log_event(
        logger,
        logging.INFO,
        "custom_game_request.admin_email.sent",
        custom_game_request_id=custom_game_request.id,
        payment_account_no=custom_game_request.payment_account_no,
        recipients_count=len(recipients),
    )


def send_custom_game_download_email(*, custom_game_request, download_token, raw_token: str) -> None:
    subject = f"Ссылка на скачивание игры {custom_game_request.payment_account_no}"
    context = {
        **_build_email_context(custom_game_request),
        "download_token": download_token,
        "download_url": build_absolute_url(
            reverse("custom-game-download", kwargs={"token": raw_token}),
        ),
        "custom_game_download_expire_hours": settings.GUEST_ACCESS_EXPIRE_HOURS,
        "custom_game_download_max_downloads": settings.GUEST_ACCESS_MAX_DOWNLOADS,
    }
    text_body = render_to_string("custom_games/email/download_message.txt", context)
    html_body = render_to_string("custom_games/email/download_message.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[custom_game_request.contact_email],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()
    download_token.sent_at = timezone.now()
    download_token.save(update_fields=["sent_at", "updated_at"])
    log_event(
        logger,
        logging.INFO,
        "custom_game_request.download_email.sent",
        custom_game_request_id=custom_game_request.id,
        payment_account_no=custom_game_request.payment_account_no,
        download_token_id=download_token.id,
        email=custom_game_request.contact_email,
    )


def _build_email_context(custom_game_request) -> dict:
    return {
        "request": custom_game_request,
        "site_base_url": settings.SITE_BASE_URL.rstrip("/"),
        "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        "admin_url": build_absolute_url(
            reverse("admin:custom_games_customgamerequest_change", args=[custom_game_request.id]),
        ),
    }
