from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Chat, Message, User

from bot.telegram_bot.config import Settings, get_settings
from bot.telegram_bot.support.app import build_webhook_path, build_webhook_url
from bot.telegram_bot.support.delivery import (
    _needs_forward,
    deliver_inbound_support_message,
    deliver_staff_reply_to_customer,
    extract_user_id_from_forward,
)
from bot.telegram_bot.support.filters import SupportStaffFilter
from bot.telegram_bot.support.formatters import (
    format_support_inbound_header,
    format_support_outbound_to_customer,
)
from bot.telegram_bot.support.mapping import lookup_support_reply_target, save_support_reply_target
from bot.telegram_bot.telegram.routes import TelegramRoute


class FormattersTests(unittest.TestCase):
    def _build_message(self, *, text: str = "Не могу скачать игру") -> Message:
        user = User(id=123456789, is_bot=False, first_name="Anna")
        chat = Chat(id=123456789, type="private")
        return Message(
            message_id=42,
            date=datetime(2026, 7, 23, 20, 15, tzinfo=UTC),
            chat=chat,
            from_user=user,
            text=text,
        )

    def test_format_support_inbound_header_contains_client_and_message(self) -> None:
        rendered = format_support_inbound_header(message=self._build_message())

        self.assertIn("Обращение в поддержку", rendered)
        self.assertIn("123456789", rendered)
        self.assertIn("Не могу скачать игру", rendered)
        self.assertIn("<blockquote>", rendered)

    def test_format_support_outbound_to_customer_escapes_html(self) -> None:
        rendered = format_support_outbound_to_customer(text="Попробуйте <script>")

        self.assertIn("Ответ поддержки PalinGames", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


class MappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_and_lookup_support_reply_target(self) -> None:
        redis = AsyncMock()
        redis.get.return_value = "123456789"

        await save_support_reply_target(
            redis=redis,
            forum_chat_id="-1001",
            message_id=10,
            telegram_user_id=123456789,
            ttl_sec=60,
        )
        user_id = await lookup_support_reply_target(
            redis=redis,
            forum_chat_id="-1001",
            message_id=10,
        )

        redis.set.assert_awaited_once_with("support:reply:-1001:10", value="123456789", ex=60)
        self.assertEqual(user_id, 123456789)

    async def test_lookup_returns_none_when_missing(self) -> None:
        redis = AsyncMock()
        redis.get.return_value = None

        user_id = await lookup_support_reply_target(
            redis=redis,
            forum_chat_id="-1001",
            message_id=99,
        )

        self.assertIsNone(user_id)


class SupportStaffFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_accepts_reply_in_support_topic(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_FORUM_CHAT_ID": "-1003754036219",
                "TELEGRAM_SUPPORT_THREAD_ID": "2",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            message = Message(
                message_id=5,
                date=datetime(2026, 7, 23, 20, 15, tzinfo=UTC),
                chat=Chat(id=-1003754036219, type="supergroup"),
                message_thread_id=2,
                from_user=User(id=999, is_bot=False, first_name="Staff"),
                reply_to_message=Message(
                    message_id=4,
                    date=datetime(2026, 7, 23, 20, 14, tzinfo=UTC),
                    chat=Chat(id=-1003754036219, type="supergroup"),
                    text="header",
                ),
                text="Ответ клиенту",
            )

            self.assertTrue(await SupportStaffFilter()(message))

    async def test_rejects_other_topic(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_FORUM_CHAT_ID": "-1003754036219",
                "TELEGRAM_SUPPORT_THREAD_ID": "2",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            message = Message(
                message_id=5,
                date=datetime(2026, 7, 23, 20, 15, tzinfo=UTC),
                chat=Chat(id=-1003754036219, type="supergroup"),
                message_thread_id=3,
                from_user=User(id=999, is_bot=False, first_name="Staff"),
                reply_to_message=Message(
                    message_id=4,
                    date=datetime(2026, 7, 23, 20, 14, tzinfo=UTC),
                    chat=Chat(id=-1003754036219, type="supergroup"),
                    text="header",
                ),
                text="Ответ клиенту",
            )

            self.assertFalse(await SupportStaffFilter()(message))


class DeliveryHelpersTests(unittest.TestCase):
    def test_needs_forward_for_photo(self) -> None:
        message = MagicMock()
        message.content_type = "photo"
        message.text = None

        self.assertTrue(_needs_forward(message))

    def test_needs_forward_for_short_text(self) -> None:
        message = MagicMock()
        message.content_type = "text"
        message.text = "короткий текст"

        self.assertFalse(_needs_forward(message))

    def test_extract_user_id_from_forward_legacy_field(self) -> None:
        message = MagicMock()
        message.forward_origin = None
        message.forward_from = User(id=555, is_bot=False, first_name="Client")

        self.assertEqual(extract_user_id_from_forward(message), 555)


class DeliveryFlowTests(unittest.IsolatedAsyncioTestCase):
    def _build_private_message(self, *, text: str = "Вопрос") -> Message:
        user = User(id=123456789, is_bot=False, first_name="Anna")
        chat = Chat(id=123456789, type="private")
        return Message(
            message_id=42,
            date=datetime(2026, 7, 23, 20, 15, tzinfo=UTC),
            chat=chat,
            from_user=user,
            text=text,
        )

    async def test_deliver_inbound_support_message_saves_header_mapping(self) -> None:
        message = self._build_private_message()
        bot = AsyncMock()
        header = MagicMock(message_id=100)
        bot.send_message.return_value = header
        redis = AsyncMock()
        route = TelegramRoute(chat_id="-1001", message_thread_id=2)

        await deliver_inbound_support_message(message=message, bot=bot, redis=redis, route=route)

        bot.send_message.assert_awaited_once()
        bot.forward_message.assert_not_awaited()
        redis.set.assert_awaited_once()

    async def test_deliver_staff_reply_uses_redis_mapping(self) -> None:
        bot = AsyncMock()
        sent = MagicMock(message_id=200)
        bot.send_message.return_value = sent
        redis = AsyncMock()
        redis.get.return_value = "123456789"

        message = MagicMock()
        message.from_user = User(id=999, is_bot=False, first_name="Staff")
        message.text = "Ответ клиенту"
        message.chat.id = -1001
        message.message_thread_id = 2
        message.reply_to_message = MagicMock(message_id=100)
        message.reply = AsyncMock()

        await deliver_staff_reply_to_customer(message=message, bot=bot, redis=redis, bot_id=1)

        bot.send_message.assert_awaited_once_with(
            chat_id=123456789,
            text=format_support_outbound_to_customer(text="Ответ клиенту"),
        )
        message.reply.assert_not_awaited()

    async def test_deliver_staff_reply_notifies_when_customer_missing(self) -> None:
        bot = AsyncMock()
        redis = AsyncMock()
        redis.get.return_value = None

        message = MagicMock()
        message.from_user = User(id=999, is_bot=False, first_name="Staff")
        message.text = "Ответ клиенту"
        message.chat.id = -1001
        message.message_thread_id = 2
        message.reply_to_message = MagicMock(message_id=100, forward_origin=None, forward_from=None)
        message.reply = AsyncMock()

        await deliver_staff_reply_to_customer(message=message, bot=bot, redis=redis, bot_id=1)

        bot.send_message.assert_not_awaited()
        message.reply.assert_awaited_once()


class AppConfigTests(unittest.TestCase):
    def test_build_webhook_path(self) -> None:
        settings = Settings(
            telegram_webhook_secret_path="secret-token",
            telegram_webhook_base_url="https://palingames.by",
        )

        self.assertEqual(build_webhook_path(settings), "/telegram/webhook/secret-token")
        self.assertEqual(
            build_webhook_url(settings),
            "https://palingames.by/telegram/webhook/secret-token",
        )

    def test_build_webhook_path_requires_secret(self) -> None:
        settings = Settings(telegram_webhook_secret_path="")

        with self.assertRaises(ValueError):
            build_webhook_path(settings)


if __name__ == "__main__":
    unittest.main()
