from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.telegram_bot.config import get_settings
from bot.telegram_bot.support.report import router as report_router

FORUM_ID = -1003754036219
BOT_USER = User(id=1, is_bot=True, first_name="PalinGames", username="palingames_bot")
STAFF = User(id=999, is_bot=False, first_name="Staff")


def _message(*, chat_id: int, chat_type: str, text: str = "/report", thread_id: int | None = None) -> Message:
    return Message(
        message_id=10,
        date=datetime(2026, 11, 27, 12, 0, tzinfo=UTC),
        chat=Chat(id=chat_id, type=chat_type),
        message_thread_id=thread_id,
        from_user=STAFF,
        text=text,
        entities=[{"type": "bot_command", "offset": 0, "length": len(text.split(maxsplit=1)[0])}],
    )


class ReportBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"TELEGRAM_FORUM_CHAT_ID": str(FORUM_ID)}, clear=False)
        self.env.start()
        get_settings.cache_clear()
        self.redis = AsyncMock()
        self.redis.set.return_value = True
        self.bot = Bot(token="123456:TESTTOKEN")
        self.dp = Dispatcher()
        self.dp.include_router(report_router)
        self.dp["redis"] = self.redis
        me = patch.object(Bot, "me", AsyncMock(return_value=BOT_USER))
        me.start()
        self.addCleanup(me.stop)

    def tearDown(self) -> None:
        report_router._parent_router = None  # module-level router is reused across tests
        self.env.stop()
        get_settings.cache_clear()

    async def _feed_message(self, message: Message) -> AsyncMock:
        answer = AsyncMock()
        with patch.object(Message, "answer", answer):
            await self.dp.feed_update(self.bot, Update(update_id=1, message=message))
        return answer

    async def _feed_callback(self, *, chat_id: int, chat_type: str, period: str = "today") -> AsyncMock:
        callback = CallbackQuery(
            id="cb1",
            from_user=STAFF,
            chat_instance="x",
            data=f"inv_report:{period}",
            message=_message(chat_id=chat_id, chat_type=chat_type),
        )
        answer = AsyncMock()
        with patch.object(CallbackQuery, "answer", answer):
            await self.dp.feed_update(self.bot, Update(update_id=2, callback_query=callback))
        return answer

    async def test_report_in_forum_answers_with_four_buttons(self) -> None:
        answer = await self._feed_message(_message(chat_id=FORUM_ID, chat_type="supergroup", thread_id=3))

        answer.assert_awaited_once()
        self.assertEqual(answer.await_args.args[0], "Выберите период:")
        keyboard = answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(
            [[b.text for b in row] for row in keyboard],
            [
                ["Сегодня", "Текущий месяц"],
                ["Прошлый месяц", "Всё время"],
            ],
        )
        self.assertEqual(keyboard[0][0].callback_data, "inv_report:today")
        self.assertEqual(keyboard[1][1].callback_data, "inv_report:all_time")

    async def test_report_with_bot_mention_in_forum(self) -> None:
        answer = await self._feed_message(
            _message(chat_id=FORUM_ID, chat_type="supergroup", text="/report@palingames_bot"),
        )
        answer.assert_awaited_once()

    async def test_report_in_private_chat_is_ignored(self) -> None:
        answer = await self._feed_message(_message(chat_id=123, chat_type="private"))
        answer.assert_not_awaited()
        self.redis.xadd.assert_not_awaited()

    async def test_report_in_foreign_chat_is_ignored(self) -> None:
        answer = await self._feed_message(_message(chat_id=-100555, chat_type="supergroup"))
        answer.assert_not_awaited()
        self.redis.xadd.assert_not_awaited()

    async def test_callback_in_forum_writes_stream_and_answers(self) -> None:
        answer = await self._feed_callback(chat_id=FORUM_ID, chat_type="supergroup", period="current_month")

        self.redis.set.assert_awaited_once_with("telegram:invoice_report:999:current_month", "1", nx=True, ex=10)
        self.redis.xadd.assert_awaited_once()
        stream, fields = self.redis.xadd.await_args.args
        self.assertEqual(stream, "telegram:commands")
        self.assertEqual(fields["command"], "invoice_report")
        self.assertEqual(fields["period"], "current_month")
        self.assertEqual(fields["requested_by_id"], "999")
        answer.assert_awaited_once_with("Отчёт отправляется в «Уведомления»")

    async def test_repeated_callback_is_rate_limited(self) -> None:
        self.redis.set.return_value = None
        answer = await self._feed_callback(chat_id=FORUM_ID, chat_type="supergroup")

        self.redis.xadd.assert_not_awaited()
        answer.assert_awaited_once_with("Отчёт уже формируется")

    async def test_callback_outside_forum_is_ignored(self) -> None:
        answer = await self._feed_callback(chat_id=-100555, chat_type="supergroup")

        answer.assert_awaited_once_with("Недоступно", show_alert=False)
        self.redis.set.assert_not_awaited()
        self.redis.xadd.assert_not_awaited()

    async def test_unknown_period_is_rejected_with_answer(self) -> None:
        answer = await self._feed_callback(chat_id=FORUM_ID, chat_type="supergroup", period="year")

        answer.assert_awaited_once_with("Недоступно", show_alert=False)
        self.redis.xadd.assert_not_awaited()

    async def test_callback_without_message_is_rejected_with_answer(self) -> None:
        callback = CallbackQuery(id="cb1", from_user=STAFF, chat_instance="x", data="inv_report:today")
        answer = AsyncMock()
        with patch.object(CallbackQuery, "answer", answer):
            await self.dp.feed_update(self.bot, Update(update_id=3, callback_query=callback))

        answer.assert_awaited_once_with("Недоступно", show_alert=False)
        self.redis.xadd.assert_not_awaited()

    async def test_xadd_failure_resets_rate_limit_and_next_request_passes(self) -> None:
        self.redis.xadd.side_effect = [ConnectionError("redis down"), "1-0"]

        first = await self._feed_callback(chat_id=FORUM_ID, chat_type="supergroup")

        first.assert_awaited_once_with("Не удалось запросить отчёт, попробуйте ещё раз")
        self.redis.delete.assert_awaited_once_with("telegram:invoice_report:999:today")

        second = await self._feed_callback(chat_id=FORUM_ID, chat_type="supergroup")

        second.assert_awaited_once_with("Отчёт отправляется в «Уведомления»")
        self.assertEqual(self.redis.xadd.await_count, 2)
