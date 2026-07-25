from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import Chat, Message

from bot.telegram_bot.config import Settings
from bot.telegram_bot.outbound.consumer import (
    deliver_outbound_message,
    ensure_consumer_group,
    process_outbound_entry,
    publish_delivery_ack,
    publish_delivery_failed,
)
from bot.telegram_bot.outbound.messages import OutboundMessage, parse_outbound_fields
from bot.telegram_bot.telegram.client import send_message
from bot.telegram_bot.telegram.errors import PermanentTelegramDeliveryError, TransientTelegramDeliveryError
from bot.telegram_bot.telegram.routes import TelegramRoute


class ParseOutboundFieldsTests(unittest.TestCase):
    def test_parse_valid_fields(self) -> None:
        message = parse_outbound_fields(
            {
                "source": "outbox",
                "destination": "notifications",
                "text": "<b>Hello</b>",
                "parse_mode": "HTML",
                "correlation_id": "42",
            },
        )

        self.assertEqual(message.source, "outbox")
        self.assertEqual(message.destination, "notifications")
        self.assertEqual(message.text, "<b>Hello</b>")
        self.assertEqual(message.parse_mode, "HTML")
        self.assertEqual(message.correlation_id, "42")

    def test_parse_requires_destination_and_text(self) -> None:
        with self.assertRaisesRegex(Exception, "missing destination"):
            parse_outbound_fields({"text": "hello"})

        with self.assertRaisesRegex(Exception, "missing text"):
            parse_outbound_fields({"destination": "notifications"})


class SendMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_returns_telegram_message_id(self) -> None:
        bot = AsyncMock()
        bot.send_message.return_value = Message(
            message_id=999,
            date=MagicMock(),
            chat=Chat(id=-1001, type="supergroup"),
        )

        message_id = await send_message(
            bot=bot,
            route=TelegramRoute(chat_id="-1001", message_thread_id=3),
            text="<b>Test</b>",
        )

        self.assertEqual(message_id, 999)
        bot.send_message.assert_awaited_once()

    async def test_send_message_maps_retry_after_to_transient_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramRetryAfter(method="sendMessage", message="slow down", retry_after=3)

        with self.assertRaises(TransientTelegramDeliveryError):
            await send_message(
                bot=bot,
                route=TelegramRoute(chat_id="-1001", message_thread_id=3),
                text="test",
            )

    async def test_send_message_maps_bad_request_to_permanent_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramBadRequest(method="sendMessage", message="bad html")

        with self.assertRaises(PermanentTelegramDeliveryError):
            await send_message(
                bot=bot,
                route=TelegramRoute(chat_id="-1001", message_thread_id=3),
                text="test",
            )

    async def test_send_message_maps_network_error_to_transient_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramNetworkError(method="sendMessage", message="timeout")

        with self.assertRaises(TransientTelegramDeliveryError):
            await send_message(
                bot=bot,
                route=TelegramRoute(chat_id="-1001", message_thread_id=3),
                text="test",
            )


class DeliverOutboundMessageTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> Settings:
        return Settings(
            telegram_forum_chat_id="-1001",
            telegram_notifications_thread_id=3,
            telegram_incidents_thread_id=9,
            telegram_outbound_ack_stream="telegram:outbound:ack",
            telegram_outbound_failed_stream="telegram:outbound:failed",
            telegram_max_retries=2,
            telegram_retry_base_sec=0.01,
        )

    def _route_patch(self, *, message_thread_id: int = 3):
        return patch(
            "bot.telegram_bot.outbound.consumer.get_telegram_route",
            return_value=TelegramRoute(chat_id="-1001", message_thread_id=message_thread_id),
        )

    async def test_deliver_publishes_ack_on_success(self) -> None:
        bot = AsyncMock()
        redis = AsyncMock()
        message = OutboundMessage(
            source="outbox",
            destination="notifications",
            text="<b>Hello</b>",
            parse_mode="HTML",
            correlation_id="42",
        )

        with (
            self._route_patch(),
            patch(
                "bot.telegram_bot.outbound.consumer.send_message",
                new=AsyncMock(return_value=1001),
            ) as send_message_mock,
        ):
            await deliver_outbound_message(
                bot=bot,
                redis=redis,
                settings=self._settings(),
                message=message,
            )

        send_message_mock.assert_awaited_once()
        redis.xadd.assert_awaited_once()
        ack_fields = redis.xadd.await_args.args[1]
        self.assertEqual(ack_fields["source"], "outbox")
        self.assertEqual(ack_fields["correlation_id"], "42")
        self.assertEqual(ack_fields["telegram_message_id"], "1001")

    async def test_deliver_publishes_failed_after_transient_retries_exhausted(self) -> None:
        bot = AsyncMock()
        redis = AsyncMock()
        message = OutboundMessage(
            source="incident",
            destination="incidents",
            text="alert",
            parse_mode="HTML",
            correlation_id="ep-webhook",
        )

        with (
            self._route_patch(message_thread_id=9),
            patch(
                "bot.telegram_bot.outbound.consumer.send_message",
                new=AsyncMock(side_effect=TransientTelegramDeliveryError("timeout")),
            ),
        ):
            await deliver_outbound_message(
                bot=bot,
                redis=redis,
                settings=self._settings(),
                message=message,
            )

        redis.xadd.assert_awaited_once()
        self.assertEqual(redis.xadd.await_args.args[0], "telegram:outbound:failed")
        failed_fields = redis.xadd.await_args.args[1]
        self.assertEqual(failed_fields["source"], "incident")
        self.assertEqual(failed_fields["correlation_id"], "ep-webhook")

    async def test_deliver_publishes_failed_on_permanent_error(self) -> None:
        bot = AsyncMock()
        redis = AsyncMock()
        message = OutboundMessage(
            source="outbox",
            destination="notifications",
            text="bad",
            parse_mode="HTML",
            correlation_id="7",
        )

        with (
            self._route_patch(),
            patch(
                "bot.telegram_bot.outbound.consumer.send_message",
                new=AsyncMock(side_effect=PermanentTelegramDeliveryError("forbidden")),
            ),
        ):
            await deliver_outbound_message(
                bot=bot,
                redis=redis,
                settings=self._settings(),
                message=message,
            )

        redis.xadd.assert_awaited_once()
        self.assertEqual(redis.xadd.await_args.args[0], "telegram:outbound:failed")


class ProcessOutboundEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_invalid_message_publishes_failed(self) -> None:
        bot = AsyncMock()
        redis = AsyncMock()
        settings = Settings(
            telegram_outbound_failed_stream="telegram:outbound:failed",
        )

        await process_outbound_entry(
            bot=bot,
            redis=redis,
            settings=settings,
            stream_id="1-0",
            fields={"source": "outbox", "correlation_id": "1"},
        )

        redis.xadd.assert_awaited_once()
        self.assertEqual(redis.xadd.await_args.args[0], "telegram:outbound:failed")


class EnsureConsumerGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_group_when_missing(self) -> None:
        redis = AsyncMock()

        await ensure_consumer_group(redis=redis, stream="telegram:outbound", group="telegram-bot")

        redis.xgroup_create.assert_awaited_once_with(
            "telegram:outbound",
            "telegram-bot",
            id="0",
            mkstream=True,
        )

    async def test_ignore_busygroup_error(self) -> None:
        from redis.exceptions import ResponseError

        redis = AsyncMock()
        redis.xgroup_create.side_effect = ResponseError("BUSYGROUP Consumer Group name already exists")

        await ensure_consumer_group(redis=redis, stream="telegram:outbound", group="telegram-bot")

        redis.xgroup_create.assert_awaited_once()


class PublishHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_delivery_ack(self) -> None:
        redis = AsyncMock()
        settings = Settings(telegram_outbound_ack_stream="telegram:outbound:ack")
        message = OutboundMessage(
            source="outbox",
            destination="notifications",
            text="x",
            parse_mode="HTML",
            correlation_id="10",
        )

        await publish_delivery_ack(
            redis=redis,
            settings=settings,
            message=message,
            telegram_message_id=555,
        )

        redis.xadd.assert_awaited_once()
        self.assertEqual(redis.xadd.await_args.args[0], "telegram:outbound:ack")

    async def test_publish_delivery_failed_truncates_error(self) -> None:
        redis = AsyncMock()
        settings = Settings(telegram_outbound_failed_stream="telegram:outbound:failed")
        message = OutboundMessage(
            source="outbox",
            destination="notifications",
            text="x",
            parse_mode="HTML",
            correlation_id="10",
        )

        await publish_delivery_failed(
            redis=redis,
            settings=settings,
            message=message,
            error="x" * 600,
        )

        failed_fields = redis.xadd.await_args.args[1]
        self.assertEqual(len(failed_fields["error"]), 512)


if __name__ == "__main__":
    unittest.main()
