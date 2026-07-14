from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_file=".env",
        extra="ignore",
    )

    # Telegram (same names as deploy/env.example)
    telegram_log_level: str = "INFO"
    telegram_bot_token: str = ""
    telegram_forum_chat_id: str = ""
    telegram_notifications_thread_id: int = 0
    telegram_support_thread_id: int = 0
    telegram_incidents_thread_id: int = 0

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    telegram_outbound_stream: str = "telegram:outbound"
    telegram_consumer_group: str = "telegram-bot"

    telegram_outbound_enabled: bool = False
    telegram_support_enabled: bool = False
    telegram_webhook_host: str = "0.0.0.0"
    telegram_webhook_port: int = 8080
    # secret path segment, e.g. "a1b2c3..." — full URL = base_url + path
    telegram_webhook_secret_path: str = ""
    # public URL without trailing slash, e.g. https://palingames.by
    telegram_webhook_base_url: str = ""
    # optional: Telegram secret_token header validation
    telegram_webhook_secret_token: str = ""

    # Delivery tuning
    telegram_send_timeout_sec: int = 10
    telegram_max_retries: int = 5
    telegram_retry_base_sec: float = 1.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
