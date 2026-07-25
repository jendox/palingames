from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from redis.asyncio import Redis
from redis.exceptions import RedisError

from bot.telegram_bot.config import Settings
from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.outbound.consumer import run_outbound_consumer
from bot.telegram_bot.support.handlers import router

logger = logging.getLogger("telegram.support")


async def health_handler(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def build_webhook_path(settings: Settings) -> str:
    secret_path = settings.telegram_webhook_secret_path.strip("/")
    if not secret_path:
        log_event(
            logger,
            logging.ERROR,
            "telegram.webhook.secret_path_not_configured",
        )
        raise ValueError("TELEGRAM_WEBHOOK_SECRET_PATH is not configured")
    return f"/telegram/webhook/{secret_path}"


def build_webhook_url(settings: Settings) -> str:
    base_url = settings.telegram_webhook_base_url.rstrip("/")
    if not base_url:
        log_event(
            logger,
            logging.ERROR,
            "telegram.webhook.base_url_not_configured",
        )
        raise ValueError("TELEGRAM_WEBHOOK_BASE_URL is not configured")
    return f"{base_url}{build_webhook_path(settings)}"


async def on_startup(bot: Bot, settings: Settings) -> None:
    webhook_url = build_webhook_url(settings)
    try:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret_token or None,
            drop_pending_updates=True,
        )
        log_event(
            logger,
            logging.INFO,
            "telegram.webhook.set",
            webhook_url=webhook_url,
        )
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "telegram.webhook.set_failed",
            webhook_url=webhook_url,
            exc_info=True,
        )
        raise


async def on_shutdown(bot: Bot, settings: Settings) -> None:
    if not settings.telegram_webhook_delete_on_shutdown:
        log_event(logger, logging.INFO, "telegram.webhook.delete_skipped")
        return

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        log_event(logger, logging.INFO, "telegram.webhook.deleted")
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "telegram.webhook.delete_failed",
            exc_info=True,
        )
        raise


def _register_outbound_lifecycle(
    *,
    app: web.Application,
    bot: Bot,
    redis: Redis,
    settings: Settings,
) -> None:
    stop_event = asyncio.Event()
    app["outbound_stop_event"] = stop_event

    async def _start_outbound(_app: web.Application) -> None:
        await redis.ping()
        task = asyncio.create_task(
            run_outbound_consumer(
                bot=bot,
                redis=redis,
                settings=settings,
                stop_event=stop_event,
            ),
            name="telegram-outbound-consumer",
        )
        _app["outbound_task"] = task
        log_event(logger, logging.INFO, "telegram.outbound.task_started")

    async def _stop_outbound(_app: web.Application) -> None:
        stop_event.set()
        task = _app.get("outbound_task")
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log_event(logger, logging.INFO, "telegram.outbound.task_stopped")

    app.on_startup.append(_start_outbound)
    app.on_cleanup.append(_stop_outbound)


def _create_support_app(
    *,
    app: web.Application,
    bot: Bot,
    redis: Redis,
    settings: Settings,
) -> None:
    dp = Dispatcher()
    dp.include_router(router)
    dp["redis"] = redis

    async def _on_startup() -> None:
        try:
            await redis.ping()
        except RedisError:
            log_event(logger, logging.ERROR, "telegram.redis.ping_failed", exc_info=True)
            raise

        me = await bot.get_me()
        dp["bot_id"] = me.id
        log_event(logger, logging.INFO, "telegram.bot.ready", bot_id=me.id, bot_username=me.username)

        await on_startup(bot, settings)

    async def _on_shutdown() -> None:
        await on_shutdown(bot, settings)
        await redis.aclose()

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    webhook_path = build_webhook_path(settings)
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.telegram_webhook_secret_token or None,
    ).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)


def _create_outbound_only_lifecycle(
    *,
    app: web.Application,
    bot: Bot,
    redis: Redis,
) -> None:
    async def _on_startup(_app: web.Application) -> None:
        try:
            await redis.ping()
        except RedisError:
            log_event(logger, logging.ERROR, "telegram.redis.ping_failed", exc_info=True)
            raise

        me = await bot.get_me()
        log_event(logger, logging.INFO, "telegram.bot.ready", bot_id=me.id, bot_username=me.username)

    async def _on_cleanup(_app: web.Application) -> None:
        await bot.session.close()
        await redis.aclose()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)


def create_app(settings: Settings) -> web.Application:
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    redis = Redis.from_url(settings.telegram_redis_url, decode_responses=True)

    app = web.Application()
    app.router.add_get("/health", health_handler)
    app["bot"] = bot
    app["redis"] = redis
    app["settings"] = settings

    if settings.telegram_outbound_enabled:
        _register_outbound_lifecycle(app=app, bot=bot, redis=redis, settings=settings)

    if settings.telegram_support_enabled:
        _create_support_app(app=app, bot=bot, redis=redis, settings=settings)
    else:
        _create_outbound_only_lifecycle(app=app, bot=bot, redis=redis)

    return app


def run_bot(settings: Settings) -> None:
    app = create_app(settings)
    log_event(
        logger,
        logging.INFO,
        "telegram.bot.listening",
        host=settings.telegram_webhook_host,
        port=settings.telegram_webhook_port,
        support=settings.telegram_support_enabled,
        outbound=settings.telegram_outbound_enabled,
    )
    web.run_app(
        app,
        host=settings.telegram_webhook_host,
        port=settings.telegram_webhook_port,
    )


def run_webhook(settings: Settings) -> None:
    run_bot(settings)
