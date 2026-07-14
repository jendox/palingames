from __future__ import annotations

import logging

from bot.telegram_bot.config import get_settings
from bot.telegram_bot.logging_setup import setup_logging, log_event
from bot.telegram_bot.support.app import run_webhook

logger = logging.getLogger("telegram")


def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.telegram_log_level)

    if not settings.telegram_support_enabled:
        log_event(logger, logging.WARNING, "telegram.bot.disabled", reason="support_disabled")
        return

    log_event(
        logger,
        logging.INFO,
        "telegram.bot.started",
        support=True,
        webhook_base_url=settings.telegram_webhook_base_url,
    )
    run_webhook(settings)


if __name__ == "__main__":
    main()
