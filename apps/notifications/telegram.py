from __future__ import annotations

from dataclasses import dataclass

import httpx
from django.conf import settings

from apps.notifications.destinations import TelegramDestination


class TelegramConfigurationError(RuntimeError):
    pass


class TelegramDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramRoute:
    chat_id: str
    message_thread_id: int


def get_telegram_route(destination: TelegramDestination) -> TelegramRoute:
    chat_id = settings.TELEGRAM_FORUM_CHAT_ID
    if not chat_id:
        raise TelegramConfigurationError("TELEGRAM_FORUM_CHAT_ID is not configured")

    telegram_thread_map: dict[TelegramDestination, int] = {
        TelegramDestination.NOTIFICATIONS: settings.TELEGRAM_NOTIFICATIONS_THREAD_ID,
        TelegramDestination.SUPPORT: settings.TELEGRAM_SUPPORT_THREAD_ID,
    }
    try:
        thread_id = telegram_thread_map[destination]
    except KeyError as error:
        raise TelegramConfigurationError(f"Unsupported telegram destination: {destination}") from error
    if not thread_id:
        raise TelegramConfigurationError(
            f"Telegram thread is not configured for destination: {destination}",
        )
    return TelegramRoute(chat_id=chat_id, message_thread_id=thread_id)


def send_telegram_message(*, destination: TelegramDestination, text: str) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramConfigurationError("TELEGRAM_BOT_TOKEN is not configured")

    route = get_telegram_route(destination)
    response = httpx.post(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": route.chat_id,
            "message_thread_id": route.message_thread_id,
            "text": text,
        },
        timeout=10,
    )
    response.raise_for_status()

    payload = response.json()
    if not payload.get("ok"):
        raise TelegramDeliveryError(f"Telegram API returned an error: {payload}")
