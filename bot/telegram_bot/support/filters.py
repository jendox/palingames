from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.telegram_bot.config import get_settings


class SupportStaffFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        settings = get_settings()
        if not settings.telegram_forum_chat_id or settings.telegram_support_thread_id <= 0:
            return False

        if message.chat.type not in {"supergroup", "group"}:
            return False

        if message.message_thread_id != settings.telegram_support_thread_id:
            return False

        if message.reply_to_message is None:
            return False

        if message.text is None or not message.text.strip():
            return False

        return message.chat.id == int(settings.telegram_forum_chat_id)
