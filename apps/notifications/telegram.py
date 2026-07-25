from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import redis
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


@lru_cache(maxsize=1)
def get_telegram_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.TELEGRAM_REDIS_URL, decode_responses=True)


def get_telegram_route(destination: TelegramDestination) -> TelegramRoute:
    chat_id = settings.TELEGRAM_FORUM_CHAT_ID
    if not chat_id:
        raise TelegramConfigurationError("TELEGRAM_FORUM_CHAT_ID is not configured")

    telegram_thread_map: dict[TelegramDestination, int] = {
        TelegramDestination.NOTIFICATIONS: settings.TELEGRAM_NOTIFICATIONS_THREAD_ID,
        TelegramDestination.SUPPORT: settings.TELEGRAM_SUPPORT_THREAD_ID,
        TelegramDestination.INCIDENTS: settings.TELEGRAM_INCIDENTS_THREAD_ID,
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


def get_telegram_destination_skip_reason(destination: TelegramDestination) -> str | None:
    if not settings.TELEGRAM_REDIS_URL:
        return "telegram_redis_not_configured"

    if not settings.TELEGRAM_FORUM_CHAT_ID:
        return "telegram_forum_chat_not_configured"

    if destination == TelegramDestination.NOTIFICATIONS and not settings.TELEGRAM_NOTIFICATIONS_THREAD_ID:
        return "telegram_notifications_route_not_configured"

    if destination == TelegramDestination.SUPPORT and not settings.TELEGRAM_SUPPORT_THREAD_ID:
        return "telegram_support_route_not_configured"

    if destination == TelegramDestination.INCIDENTS and not settings.TELEGRAM_INCIDENTS_THREAD_ID:
        return "telegram_incidents_route_not_configured"

    return None


def publish_telegram_outbound(
    *,
    destination: TelegramDestination,
    text: str,
    source: str,
    correlation_id: str = "",
    parse_mode: str = "HTML",
) -> str:
    client = get_telegram_redis_client()
    stream_id = client.xadd(
        settings.TELEGRAM_OUTBOUND_STREAM,
        {
            "source": source,
            "destination": destination.value,
            "text": text,
            "parse_mode": parse_mode,
            "correlation_id": correlation_id,
        },
    )
    return stream_id


def send_telegram_message(
    *,
    destination: TelegramDestination,
    text: str,
    source: str = "incident",
    correlation_id: str = "",
    parse_mode: str = "HTML",
) -> None:
    publish_telegram_outbound(
        destination=destination,
        text=text,
        source=source,
        correlation_id=correlation_id,
        parse_mode=parse_mode,
    )
