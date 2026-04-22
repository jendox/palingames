from __future__ import annotations

from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.custom_games.models import CustomGameRequest
from apps.products.models import Review


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


def format_review_submitted_admin_telegram(*, review: Review) -> str:
    admin_url = build_absolute_url(
        reverse("admin:products_review_change", args=[review.id]),
    )

    return (
        "Новый отзыв на товар\n"
        f"Товар: {review.product.title}\n"
        f"Пользователь: {review.user.email}\n"
        f"Оценка: {review.rating}\n"
        f"Текст: {review.comment or '-'}\n"
        f"Админка: {admin_url}"
    )
