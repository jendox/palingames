from __future__ import annotations

from redis.asyncio import Redis

from bot.telegram_bot.config import get_settings

_CACHE_KEY = "support:reply:{chat_id}:{message_id}"


def _get_key(chat_id: str, message_id: int) -> str:
    return _CACHE_KEY.format(chat_id=chat_id, message_id=message_id)


async def save_support_reply_target(
    *,
    redis: Redis,
    forum_chat_id: str,
    message_id: int,
    telegram_user_id: int,
    ttl_sec: int | None = None,
) -> None:
    if ttl_sec is None:
        ttl_sec = get_settings().support_reply_mapping_ttl_sec
    key = _get_key(forum_chat_id, message_id)
    await redis.set(key, value=str(telegram_user_id), ex=ttl_sec)


async def lookup_support_reply_target(
    *,
    redis: Redis,
    forum_chat_id: str,
    message_id: int,
) -> int | None:
    key = _get_key(forum_chat_id, message_id)
    value = await redis.get(key)
    return int(value) if value is not None else None
