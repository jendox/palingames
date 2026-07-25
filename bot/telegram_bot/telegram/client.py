from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from bot.telegram_bot.telegram.errors import (
    PermanentTelegramDeliveryError,
    TelegramConfigurationError,
    TransientTelegramDeliveryError,
)
from bot.telegram_bot.telegram.routes import TelegramRoute

logger = logging.getLogger("telegram_bot.client")


def _resolve_parse_mode(parse_mode: str) -> ParseMode:
    normalized = parse_mode.strip().upper()
    if normalized in {"", "HTML"}:
        return ParseMode.HTML
    if normalized == "MARKDOWNV2":
        return ParseMode.MARKDOWN_V2
    if normalized == "MARKDOWN":
        return ParseMode.MARKDOWN
    raise TelegramConfigurationError(f"Unsupported parse_mode: {parse_mode}")


async def send_message(
    *,
    bot: Bot,
    route: TelegramRoute,
    text: str,
    parse_mode: str = "HTML",
) -> int:
    try:
        message = await bot.send_message(
            chat_id=route.chat_id,
            message_thread_id=route.message_thread_id,
            text=text,
            parse_mode=_resolve_parse_mode(parse_mode),
        )
    except TelegramRetryAfter as exc:
        raise TransientTelegramDeliveryError(
            f"Telegram rate limit, retry after {exc.retry_after}s",
        ) from exc
    except (TelegramNetworkError, TelegramServerError) as exc:
        raise TransientTelegramDeliveryError(str(exc)) from exc
    except TelegramForbiddenError as exc:
        raise PermanentTelegramDeliveryError(str(exc)) from exc
    except TelegramBadRequest as exc:
        raise PermanentTelegramDeliveryError(str(exc)) from exc

    return message.message_id
