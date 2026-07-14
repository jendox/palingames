from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.telegram.routes import (
    TelegramDestination,
    get_telegram_destination_skip_reason,
    get_telegram_route,
)

logger = logging.getLogger("telegram.support")

router = Router(name="support")

START_TEXT = (
    "Здравствуйте! Это поддержка PaliGames.\n"
    "Напишите ваш вопрос в этом чате — мы передадим его команде.\n"
    "Срочные вопросы: support@palingames.by\n"
    "Покупки — только на сайте palingames.by"
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    if message.from_user is None:
        return

    log_event(
        logger,
        logging.INFO,
        "telegram.support.start",
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
    )
    await message.answer(START_TEXT)


@router.message(F.chat.type == "private", ~F.text.startswith("/"))
async def handle_private_message(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    skip_reason = get_telegram_destination_skip_reason(TelegramDestination.SUPPORT.value)
    if skip_reason is not None:
        log_event(
            logger,
            logging.INFO,
            "telegram.support.forward_skipped",
            reason=skip_reason,
            telegram_user_id=message.from_user.id,
        )
        await message.answer(
            "Поддержка временно недоступна. Напишите на support@palingames.by",
        )
        return

    route = get_telegram_route(TelegramDestination.SUPPORT.value)

    await bot.forward_message(
        chat_id=route.chat_id,
        message_thread_id=route.message_thread_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )

    log_event(
        logger,
        logging.INFO,
        "telegram.support.forwarded",
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        message_id=message.message_id,
    )
