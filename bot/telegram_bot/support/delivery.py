from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message
from redis.asyncio import Redis
from redis.exceptions import RedisError

from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.support.formatters import (
    format_support_inbound_header,
    format_support_outbound_to_customer,
)
from bot.telegram_bot.support.mapping import lookup_support_reply_target, save_support_reply_target
from bot.telegram_bot.telegram.routes import TelegramRoute

logger = logging.getLogger("telegram.support")

MAX_TELEGRAM_MESSAGE_LENGTH = 4096

SUPPORT_UNAVAILABLE_TEXT = "Поддержка временно недоступна. Напишите на support@palingames.by"
CUSTOMER_NOT_FOUND_TEXT = "Не удалось определить клиента. Ответьте на шапку обращения."
CUSTOMER_BLOCKED_TEXT = "Не удалось доставить: клиент заблокировал бота."
CUSTOMER_DELIVERY_FAILED_TEXT = (
    "Не удалось отправить сообщение клиенту. Попробуйте короче или напишите на support@palingames.by"
)
INTERNAL_ERROR_TEXT = "Внутренняя ошибка. Попробуйте позже."


def extract_user_id_from_forward(message: Message) -> int | None:
    origin = message.forward_origin
    if origin is not None and origin.type == "user" and origin.sender_user:
        return origin.sender_user.id
    if message.forward_from is not None:
        return message.forward_from.id
    return None


def _needs_forward(message: Message) -> bool:
    if message.content_type != "text":
        return True
    return len((message.text or "").strip()) > MAX_TELEGRAM_MESSAGE_LENGTH


async def _save_reply_mapping(
    *,
    redis: Redis,
    route: TelegramRoute,
    message_id: int,
    telegram_user_id: int,
) -> None:
    try:
        await save_support_reply_target(
            redis=redis,
            forum_chat_id=route.chat_id,
            message_id=message_id,
            telegram_user_id=telegram_user_id,
        )
        log_event(
            logger,
            logging.INFO,
            "telegram.support.inbound.mapping_saved",
            forum_message_id=message_id,
            telegram_user_id=telegram_user_id,
        )
    except RedisError:
        log_event(
            logger,
            logging.WARNING,
            "telegram.support.inbound.mapping_failed",
            forum_message_id=message_id,
            telegram_user_id=telegram_user_id,
            exc_info=True,
        )


async def deliver_inbound_support_message(
    *,
    message: Message,
    bot: Bot,
    redis: Redis,
    route: TelegramRoute,
) -> None:
    if message.from_user is None:
        return

    telegram_user_id = message.from_user.id

    try:
        header = await bot.send_message(
            chat_id=route.chat_id,
            message_thread_id=route.message_thread_id,
            text=format_support_inbound_header(message=message),
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        log_event(
            logger,
            logging.ERROR,
            "telegram.support.inbound.failed",
            step="send_header",
            telegram_user_id=telegram_user_id,
            exc_info=True,
        )
        await message.answer(SUPPORT_UNAVAILABLE_TEXT)
        return

    await _save_reply_mapping(
        redis=redis,
        route=route,
        message_id=header.message_id,
        telegram_user_id=telegram_user_id,
    )

    if _needs_forward(message):
        try:
            forwarded = await bot.forward_message(
                chat_id=route.chat_id,
                message_thread_id=route.message_thread_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            log_event(
                logger,
                logging.ERROR,
                "telegram.support.inbound.failed",
                step="forward",
                telegram_user_id=telegram_user_id,
                header_message_id=header.message_id,
                exc_info=True,
            )
        else:
            await _save_reply_mapping(
                redis=redis,
                route=route,
                message_id=forwarded.message_id,
                telegram_user_id=telegram_user_id,
            )

    log_event(
        logger,
        logging.INFO,
        "telegram.support.inbound.delivered",
        telegram_user_id=telegram_user_id,
        username=message.from_user.username,
        inbound_message_id=message.message_id,
        header_message_id=header.message_id,
        content_type=message.content_type,
    )


async def _resolve_customer_user_id(
    *,
    message: Message,
    redis: Redis,
    replied: Message,
    staff_user_id: int | None,
) -> int | None:
    try:
        user_id = await lookup_support_reply_target(
            redis=redis,
            forum_chat_id=str(message.chat.id),
            message_id=replied.message_id,
        )
    except RedisError:
        log_event(
            logger,
            logging.ERROR,
            "telegram.support.staff_reply.redis_failed",
            staff_user_id=staff_user_id,
            exc_info=True,
        )
        await message.reply(INTERNAL_ERROR_TEXT)
        return None

    if user_id is None:
        user_id = extract_user_id_from_forward(replied)

    if user_id is None:
        log_event(
            logger,
            logging.WARNING,
            "telegram.support.staff_reply.customer_not_found",
            staff_user_id=staff_user_id,
            reply_to_message_id=replied.message_id,
            forum_chat_id=message.chat.id,
        )
        await message.reply(CUSTOMER_NOT_FOUND_TEXT)

    return user_id


async def _send_customer_reply(*, message: Message, bot: Bot, user_id: int, staff_user_id: int | None) -> bool:
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=format_support_outbound_to_customer(text=message.text or ""),
        )
    except TelegramForbiddenError:
        log_event(
            logger,
            logging.WARNING,
            "telegram.support.staff_reply.delivery_failed",
            staff_user_id=staff_user_id,
            customer_user_id=user_id,
            error_code="bot_blocked",
            exc_info=True,
        )
        await message.reply(CUSTOMER_BLOCKED_TEXT)
        return False
    except TelegramBadRequest:
        log_event(
            logger,
            logging.ERROR,
            "telegram.support.staff_reply.delivery_failed",
            staff_user_id=staff_user_id,
            customer_user_id=user_id,
            error_code="bad_request",
            exc_info=True,
        )
        await message.reply(CUSTOMER_DELIVERY_FAILED_TEXT)
        return False

    log_event(
        logger,
        logging.INFO,
        "telegram.support.staff_reply.delivered",
        staff_user_id=staff_user_id,
        customer_user_id=user_id,
        outbound_message_id=sent.message_id,
    )
    return True


async def deliver_staff_reply_to_customer(
    *,
    message: Message,
    bot: Bot,
    redis: Redis,
    bot_id: int,
) -> None:
    if message.from_user is not None and message.from_user.id == bot_id:
        return

    if message.text is None or not message.text.strip():
        return

    replied = message.reply_to_message
    if replied is None:
        return

    staff_user_id = message.from_user.id if message.from_user is not None else None
    log_event(
        logger,
        logging.INFO,
        "telegram.support.staff_reply.received",
        staff_user_id=staff_user_id,
        reply_to_message_id=replied.message_id,
        message_thread_id=message.message_thread_id,
    )

    user_id = await _resolve_customer_user_id(
        message=message,
        redis=redis,
        replied=replied,
        staff_user_id=staff_user_id,
    )
    if user_id is None:
        return

    await _send_customer_reply(message=message, bot=bot, user_id=user_id, staff_user_id=staff_user_id)
