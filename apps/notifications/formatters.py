from __future__ import annotations

from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.custom_games.models import CustomGameRequest


def format_custom_game_request_admin_telegram(*, custom_game_request: CustomGameRequest) -> str:
    admin_url = build_absolute_url(
        reverse(
            "admin:custom_games_customgamerequest_change",
            args=[custom_game_request.id],
        ),
    )

    return (
        "Новая заявка на игру\n"
        f"Номер: {custom_game_request.payment_account_no}\n"
        f"Имя: {custom_game_request.contact_name}\n"
        f"Email: {custom_game_request.contact_email}\n"
        f"Тема: {custom_game_request.subject}\n"
        f"Возраст: {custom_game_request.audience}\n"
        f"Страниц: {custom_game_request.page_count}\n"
        f"Идея: {custom_game_request.idea or '-'}\n"
        f"Админка: {admin_url}"
    )
