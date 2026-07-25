from __future__ import annotations

import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.urls import reverse

from apps.access.emails import build_absolute_url
from apps.custom_games.models import CustomGameRequest
from apps.payments.models import Invoice
from apps.products.models import Review


def _get_minsk_ts(dt: datetime | date | None) -> str:
    if dt is None:
        return ""
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y")
    return dt.astimezone(ZoneInfo("Europe/Minsk")).strftime("%d.%m.%Y %H:%M")


def format_custom_game_request_admin_telegram(*, custom_game_request: CustomGameRequest) -> str:
    admin_url = build_absolute_url(
        reverse(
            "admin:custom_games_customgamerequest_change",
            args=[custom_game_request.id],
        ),
    )
    lines = [
        "<b>Новая заявка на игру</b>",
        "",
        "<b>Клиент</b>",
        f"Имя: {html.escape(custom_game_request.contact_name)}",
        f"Email: {html.escape(custom_game_request.contact_email)}",
        "",
        "<b>Заявка</b>",
        f"Номер: {html.escape(custom_game_request.payment_account_no)}",
        f"Тема: {html.escape(custom_game_request.subject)}",
        f"Возраст: {html.escape(custom_game_request.audience)} · "
        f"Страниц: {html.escape(custom_game_request.page_count)}",
        "",
        "<b>Идея клиента</b>",
        f"<blockquote>{html.escape(custom_game_request.idea) or '-'}</blockquote>",
        "",
        f"<a href='{admin_url}'>Открыть в админке</a>",
        "",
        f"<i>{_get_minsk_ts(custom_game_request.created_at)}</i>",
    ]

    return "\n".join(lines)


def format_custom_game_request_paid_admin_telegram(
    *,
    custom_game_request: CustomGameRequest,
    invoice: Invoice,
) -> str:
    admin_url = build_absolute_url(
        reverse(
            "admin:custom_games_customgamerequest_change",
            args=[custom_game_request.id],
        ),
    )

    currency_label = invoice.get_currency_display() if invoice.currency is not None else "-"

    lines = [
        "<b>Оплачена заявка на игру</b>",
        "",
        "<b>Клиент</b>",
        f"Имя: {html.escape(custom_game_request.contact_name)}",
        f"Email: {html.escape(custom_game_request.contact_email)}",
        "",
        "<b>Подробности</b>",
        f"Тема: {html.escape(custom_game_request.subject)}",
        f"Номер лицевого счёта: {html.escape(custom_game_request.payment_account_no)}",
        f"Сумма: {invoice.amount} {currency_label}",
        f"Дедлайн: {_get_minsk_ts(custom_game_request.deadline) or '-'}",
        f"Статус: {custom_game_request.get_status_display()}",
        "",
        f"<a href='{admin_url}'>Открыть в админке</a>",
        "",
        f"<i>{_get_minsk_ts(custom_game_request.updated_at)}</i>",
    ]

    return "\n".join(lines)


def format_review_submitted_admin_telegram(*, review: Review) -> str:
    admin_url = build_absolute_url(
        reverse("admin:products_review_change", args=[review.id]),
    )

    lines = [
        "<b>Новый отзыв</b>",
        "",
        "<b>Клиент</b>",
        f"Email: {html.escape(review.user.email)}",
        "",
        "<b>Подробности</b>",
        f"Материал: {html.escape(review.product.title)}",
        f"Оценка: {review.rating}",
        "",
        "<b>Текст отзыва</b>",
        f"<blockquote>{html.escape(review.comment) or '-'}</blockquote>",
        "",
        f"<a href='{admin_url}'>Открыть в админке</a>",
        "",
        f"<i>{_get_minsk_ts(review.created_at)}</i>",
    ]

    return "\n".join(lines)
