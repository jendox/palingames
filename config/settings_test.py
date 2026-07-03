"""Django settings for the test suite.

Imports production settings, then clears Telegram credentials so a developer's
local .env cannot trigger real Bot API calls during ``manage.py test``.
Tests that exercise Telegram delivery must set fake tokens via ``override_settings``
and mock ``send_telegram_message``.
"""

from config.settings import *  # noqa: F403

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_FORUM_CHAT_ID = ""
TELEGRAM_NOTIFICATIONS_THREAD_ID = 0
TELEGRAM_SUPPORT_THREAD_ID = 0
TELEGRAM_INCIDENTS_THREAD_ID = 0
