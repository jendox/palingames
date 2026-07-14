from __future__ import annotations

import logging

import httpx

from bot.telegram_bot.telegram.routes import TelegramRoute
from bot.telegram_bot.config import get_settings
from bot.telegram_bot.telegram.errors import TelegramConfigurationError

logger = logging.getLogger("telegram_bot.client")

TELEGRAM_API_BASE_URL = "https://api.telegram.org"

async def send_message(
    *,
    route: TelegramRoute,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> int:
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        raise TelegramConfigurationError("TELEGRAM_BOT_TOKEN is not configured")

