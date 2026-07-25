"""Django settings for the test suite.

Imports production settings, then clears Telegram credentials so a developer's
local .env cannot trigger real Bot API calls during ``manage.py test``.
Tests that exercise Telegram delivery must configure fake routes via ``override_settings``
and mock ``send_telegram_message`` or Redis publish helpers.
"""

from config.settings import *  # noqa: F403

TEST_RUNNER = "config.test_runner.PalingamesDiscoverRunner"

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_FORUM_CHAT_ID = ""
TELEGRAM_NOTIFICATIONS_THREAD_ID = 0
TELEGRAM_SUPPORT_THREAD_ID = 0
TELEGRAM_INCIDENTS_THREAD_ID = 0
TELEGRAM_REDIS_URL = ""
