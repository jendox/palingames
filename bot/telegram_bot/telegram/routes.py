from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bot.telegram_bot.config import Settings, get_settings
from bot.telegram_bot.telegram.errors import TelegramConfigurationError


class TelegramDestination(StrEnum):
    NOTIFICATIONS = "notifications"
    SUPPORT = "support"
    INCIDENTS = "incidents"


@dataclass(frozen=True)
class TelegramRoute:
    chat_id: str
    message_thread_id: int


def parse_destination(destination: str) -> TelegramDestination:
    try:
        return TelegramDestination(destination)
    except ValueError as error:
        raise TelegramConfigurationError(
            f"Unsupported telegram destination: {destination}",
        ) from error


def get_telegram_route(destination: str) -> TelegramRoute:
    settings = get_settings()
    parsed_destination = parse_destination(destination)

    chat_id = settings.telegram_forum_chat_id
    if not chat_id:
        raise TelegramConfigurationError("TELEGRAM_FORUM_CHAT_ID is not configured")

    thread_map: dict[TelegramDestination, int] = {
        TelegramDestination.NOTIFICATIONS: settings.telegram_notifications_thread_id,
        TelegramDestination.SUPPORT: settings.telegram_support_thread_id,
        TelegramDestination.INCIDENTS: settings.telegram_incidents_thread_id,
    }

    thread_id = thread_map[parsed_destination]
    if thread_id <= 0:
        raise TelegramConfigurationError(
            f"Telegram thread is not configured for destination: {parsed_destination.value}",
        )

    return TelegramRoute(chat_id=chat_id, message_thread_id=thread_id)


def _get_settings_skip_reason(settings: Settings) -> str | None:
    if not settings.telegram_bot_token:
        return "telegram_bot_token_not_configured"

    if not settings.telegram_forum_chat_id:
        return "telegram_forum_chat_not_configured"

    return None


def _get_destination_skip_reason(destination: str, settings: Settings) -> str | None:
    try:
        parsed_destination = TelegramDestination(destination)
    except ValueError:
        return "telegram_destination_unsupported"

    if parsed_destination == TelegramDestination.NOTIFICATIONS and settings.telegram_notifications_thread_id <= 0:
        return "telegram_notifications_route_not_configured"

    if parsed_destination == TelegramDestination.SUPPORT and settings.telegram_support_thread_id <= 0:
        return "telegram_support_route_not_configured"

    if parsed_destination == TelegramDestination.INCIDENTS and settings.telegram_incidents_thread_id <= 0:
        return "telegram_incidents_route_not_configured"

    return None


def get_telegram_destination_skip_reason(destination: str) -> str | None:
    settings = get_settings()
    skip_reason = _get_settings_skip_reason(settings)
    if skip_reason is not None:
        return skip_reason

    return _get_destination_skip_reason(destination, settings)
