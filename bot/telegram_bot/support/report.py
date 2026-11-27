from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from bot.telegram_bot.config import get_settings
from bot.telegram_bot.logging_setup import log_event
from bot.telegram_bot.support.filters import ServiceForumFilter, is_service_forum_chat

logger = logging.getLogger("telegram.report")

router = Router(name="invoice_report")

CALLBACK_PREFIX = "inv_report:"
COMMAND_INVOICE_REPORT = "invoice_report"
RATE_LIMIT_TTL_SEC = 10

PERIODS = ("today", "current_month", "last_month", "all_time")

CHOOSE_PERIOD_TEXT = "Выберите период:"
SENDING_TEXT = "Отчёт отправляется в «Уведомления»"
ALREADY_RUNNING_TEXT = "Отчёт уже формируется"
UNAVAILABLE_TEXT = "Недоступно"
REQUEST_FAILED_TEXT = "Не удалось запросить отчёт, попробуйте ещё раз"


def build_period_keyboard() -> InlineKeyboardMarkup:
    def button(text: str, period: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"{CALLBACK_PREFIX}{period}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Сегодня", "today"), button("Текущий месяц", "current_month")],
            [button("Прошлый месяц", "last_month"), button("Всё время", "all_time")],
        ],
    )


@router.message(Command("report"), ServiceForumFilter())
async def handle_report_command(message: Message) -> None:
    await message.answer(CHOOSE_PERIOD_TEXT, reply_markup=build_period_keyboard())


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def handle_report_callback(callback: CallbackQuery, redis: Redis) -> None:
    message = callback.message
    if message is None or not is_service_forum_chat(message.chat.id, message.chat.type):
        # Always answer, otherwise the client keeps showing the loading spinner.
        await callback.answer(UNAVAILABLE_TEXT, show_alert=False)
        return

    period = (callback.data or "")[len(CALLBACK_PREFIX) :]
    if period not in PERIODS:
        await callback.answer(UNAVAILABLE_TEXT, show_alert=False)
        return

    user_id = callback.from_user.id
    settings = get_settings()

    rate_limit_key = f"telegram:invoice_report:{user_id}:{period}"
    acquired = await redis.set(rate_limit_key, "1", nx=True, ex=RATE_LIMIT_TTL_SEC)
    if not acquired:
        log_event(
            logger,
            logging.INFO,
            "telegram.report.rate_limited",
            telegram_user_id=user_id,
            period=period,
        )
        await callback.answer(ALREADY_RUNNING_TEXT)
        return

    try:
        await redis.xadd(
            settings.telegram_command_stream,
            {
                "command": COMMAND_INVOICE_REPORT,
                "period": period,
                "requested_by_id": str(user_id),
                "correlation_id": callback.id,
            },
        )
    except Exception:
        log_event(
            logger,
            logging.ERROR,
            "telegram.report.enqueue_failed",
            exc_info=True,
            telegram_user_id=user_id,
            period=period,
        )
        # Let the user retry immediately instead of waiting out the rate limit.
        try:
            await redis.delete(rate_limit_key)
        except Exception:
            log_event(logger, logging.ERROR, "telegram.report.rate_limit_reset_failed", exc_info=True)
        await callback.answer(REQUEST_FAILED_TEXT)
        return
    log_event(
        logger,
        logging.INFO,
        "telegram.report.requested",
        telegram_user_id=user_id,
        period=period,
    )
    await callback.answer(SENDING_TEXT)
