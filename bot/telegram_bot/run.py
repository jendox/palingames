from __future__ import annotations

import logging

from bot.telegram_bot.config import get_settings
from bot.telegram_bot.logging_setup import log_event, setup_logging
from bot.telegram_bot.support.app import run_bot

logger = logging.getLogger("telegram")


def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.telegram_log_level)

    if not settings.telegram_support_enabled and not settings.telegram_outbound_enabled:
        log_event(logger, logging.WARNING, "telegram.bot.disabled", reason="nothing_enabled")
        return

    log_event(
        logger,
        logging.INFO,
        "telegram.bot.started",
        support=settings.telegram_support_enabled,
        outbound=settings.telegram_outbound_enabled,
        webhook_base_url=settings.telegram_webhook_base_url,
    )
    run_bot(settings)


if __name__ == "__main__":
    main()
