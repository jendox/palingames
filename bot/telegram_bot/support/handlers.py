from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import ErrorEvent, Message
from redis.asyncio import Redis

from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.support.delivery import deliver_inbound_support_message, deliver_staff_reply_to_customer
from bot.telegram_bot.support.filters import SupportStaffFilter
from bot.telegram_bot.telegram.routes import (
    TelegramDestination,
    get_telegram_destination_skip_reason,
    get_telegram_route,
)

logger = logging.getLogger("telegram.support")

router = Router(name="support")

START_TEXT = (
    "Здравствуйте! Это поддержка PaliGames.\n\n"
    "Опишите ваш вопрос в этом чате — мы передадим его команде.\n"
    "Чтобы мы быстрее нашли заказ, укажите email при покупке "
    "или номер заказа.\n\n"
    "Срочные вопросы: support@palingames.by\n"
    'Покупки — только на <a href="https://palingames.by">сайте</a>'
)

SUPPORT_UNAVAILABLE_TEXT = "Поддержка временно недоступна. Напишите на support@palingames.by"


@router.errors()
async def handle_router_error(event: ErrorEvent) -> None:
    exc = event.exception
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else True
    log_event(
        logger,
        logging.ERROR,
        "telegram.support.handler_error",
        exc_info=exc_info,
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
async def handle_private_message(message: Message, bot: Bot, redis: Redis) -> None:
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
        await message.answer(SUPPORT_UNAVAILABLE_TEXT)
        return

    route = get_telegram_route(TelegramDestination.SUPPORT.value)
    await deliver_inbound_support_message(message=message, bot=bot, redis=redis, route=route)


@router.message(SupportStaffFilter())
async def handle_staff_reply(message: Message, bot: Bot, redis: Redis, bot_id: int) -> None:
    await deliver_staff_reply_to_customer(message=message, bot=bot, redis=redis, bot_id=bot_id)
