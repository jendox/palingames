from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import UTC, datetime

from aiogram import Bot
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from bot.telegram_bot.config import Settings
from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.outbound.messages import OutboundMessage, parse_outbound_fields
from bot.telegram_bot.telegram.client import send_message
from bot.telegram_bot.telegram.errors import (
    PermanentTelegramDeliveryError,
    TelegramConfigurationError,
    TransientTelegramDeliveryError,
)
from bot.telegram_bot.telegram.routes import get_telegram_route

logger = logging.getLogger("telegram.outbound")


def build_consumer_name() -> str:
    return f"bot-{socket.gethostname()}-{os.getpid()}"


async def ensure_consumer_group(*, redis: Redis, stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        log_event(
            logger,
            logging.INFO,
            "telegram.outbound.group_created",
            stream=stream,
            group=group,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def publish_delivery_ack(
    *,
    redis: Redis,
    settings: Settings,
    message: OutboundMessage,
    telegram_message_id: int,
) -> None:
    await redis.xadd(
        settings.telegram_outbound_ack_stream,
        {
            "source": message.source,
            "correlation_id": message.correlation_id,
            "telegram_message_id": str(telegram_message_id),
            "delivered_at": datetime.now(tz=UTC).isoformat(),
        },
    )


async def publish_delivery_failed(
    *,
    redis: Redis,
    settings: Settings,
    message: OutboundMessage,
    error: str,
) -> None:
    await redis.xadd(
        settings.telegram_outbound_failed_stream,
        {
            "source": message.source,
            "correlation_id": message.correlation_id,
            "error": error[:512],
            "failed_at": datetime.now(tz=UTC).isoformat(),
        },
    )


def _retry_delay_seconds(*, settings: Settings, attempt: int) -> float:
    return settings.telegram_retry_base_sec * (2 ** max(attempt - 1, 0))


async def deliver_outbound_message(
    *,
    bot: Bot,
    redis: Redis,
    settings: Settings,
    message: OutboundMessage,
) -> None:
    route = get_telegram_route(message.destination)
    last_error = "unknown delivery error"

    for attempt in range(1, settings.telegram_max_retries + 1):
        try:
            telegram_message_id = await send_message(
                bot=bot,
                route=route,
                text=message.text,
                parse_mode=message.parse_mode,
            )
        except TransientTelegramDeliveryError as exc:
            last_error = str(exc)
            log_event(
                logger,
                logging.WARNING,
                "telegram.outbound.retry",
                source=message.source,
                correlation_id=message.correlation_id,
                destination=message.destination,
                attempt=attempt,
                max_retries=settings.telegram_max_retries,
                error=last_error,
            )
            if attempt >= settings.telegram_max_retries:
                break
            await asyncio.sleep(_retry_delay_seconds(settings=settings, attempt=attempt))
            continue
        except PermanentTelegramDeliveryError as exc:
            last_error = str(exc)
            await publish_delivery_failed(
                redis=redis,
                settings=settings,
                message=message,
                error=last_error,
            )
            log_event(
                logger,
                logging.ERROR,
                "telegram.outbound.failed",
                source=message.source,
                correlation_id=message.correlation_id,
                destination=message.destination,
                attempt=attempt,
                error=last_error,
            )
            return
        except TelegramConfigurationError as exc:
            last_error = str(exc)
            await publish_delivery_failed(
                redis=redis,
                settings=settings,
                message=message,
                error=last_error,
            )
            log_event(
                logger,
                logging.ERROR,
                "telegram.outbound.failed",
                source=message.source,
                correlation_id=message.correlation_id,
                destination=message.destination,
                attempt=attempt,
                error=last_error,
            )
            return
        else:
            await publish_delivery_ack(
                redis=redis,
                settings=settings,
                message=message,
                telegram_message_id=telegram_message_id,
            )
            log_event(
                logger,
                logging.INFO,
                "telegram.outbound.delivered",
                source=message.source,
                correlation_id=message.correlation_id,
                destination=message.destination,
                telegram_message_id=telegram_message_id,
                attempt=attempt,
            )
            return

    await publish_delivery_failed(
        redis=redis,
        settings=settings,
        message=message,
        error=last_error,
    )
    log_event(
        logger,
        logging.ERROR,
        "telegram.outbound.failed",
        source=message.source,
        correlation_id=message.correlation_id,
        destination=message.destination,
        attempt=settings.telegram_max_retries,
        error=last_error,
    )


async def process_outbound_entry(
    *,
    bot: Bot,
    redis: Redis,
    settings: Settings,
    stream_id: str,
    fields: dict[str, str],
) -> None:
    try:
        message = parse_outbound_fields(fields)
    except TelegramConfigurationError as exc:
        await publish_delivery_failed(
            redis=redis,
            settings=settings,
            message=OutboundMessage(
                source=fields.get("source", ""),
                destination=fields.get("destination", ""),
                text=fields.get("text", ""),
                parse_mode=fields.get("parse_mode", "HTML"),
                correlation_id=fields.get("correlation_id", ""),
            ),
            error=str(exc),
        )
        log_event(
            logger,
            logging.ERROR,
            "telegram.outbound.invalid_message",
            stream_id=stream_id,
            error=str(exc),
        )
        return

    await deliver_outbound_message(bot=bot, redis=redis, settings=settings, message=message)


async def _read_and_process_outbound_batch(
    *,
    bot: Bot,
    redis: Redis,
    settings: Settings,
    consumer_name: str,
    stop_event: asyncio.Event,
) -> None:
    stream = settings.telegram_outbound_stream
    group = settings.telegram_consumer_group

    results = await redis.xreadgroup(
        groupname=group,
        consumername=consumer_name,
        streams={stream: ">"},
        count=10,
        block=1000,
    )
    if not results:
        return

    for _stream_name, entries in results:
        for stream_id, fields in entries:
            if stop_event.is_set():
                return
            try:
                await process_outbound_entry(
                    bot=bot,
                    redis=redis,
                    settings=settings,
                    stream_id=stream_id,
                    fields=fields,
                )
                await redis.xack(stream, group, stream_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log_event(
                    logger,
                    logging.ERROR,
                    "telegram.outbound.process_failed",
                    stream_id=stream_id,
                    exc_info=True,
                )


async def run_outbound_consumer(
    *,
    bot: Bot,
    redis: Redis,
    settings: Settings,
    stop_event: asyncio.Event,
) -> None:
    consumer_name = build_consumer_name()
    stream = settings.telegram_outbound_stream
    group = settings.telegram_consumer_group

    await ensure_consumer_group(redis=redis, stream=stream, group=group)
    log_event(
        logger,
        logging.INFO,
        "telegram.outbound.consumer_started",
        stream=stream,
        group=group,
        consumer=consumer_name,
    )

    while not stop_event.is_set():
        try:
            await _read_and_process_outbound_batch(
                bot=bot,
                redis=redis,
                settings=settings,
                consumer_name=consumer_name,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(logger, logging.ERROR, "telegram.outbound.read_failed", exc_info=True)
            await asyncio.sleep(settings.telegram_retry_base_sec)

    log_event(logger, logging.INFO, "telegram.outbound.consumer_stopped", consumer=consumer_name)
