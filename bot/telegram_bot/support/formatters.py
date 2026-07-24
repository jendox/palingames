from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from aiogram.types import Message, User

CONTENT_TYPE_LABELS: dict[str, str] = {
    "text": "текст",
    "photo": "фото",
    "document": "файл",
    "voice": "голосовое",
    "video": "видео",
    "sticker": "стикер",
    "audio": "аудио",
}


def _display_name(user: User) -> str:
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part).strip()
    return name or "Без имени"


def _username_line(user: User) -> str:
    if user.username:
        return f"@{html.escape(user.username)}"
    return "не указан"


def format_support_inbound_header(*, message: Message) -> str:
    user = message.from_user
    assert user is not None

    content_type = message.content_type
    type_label = CONTENT_TYPE_LABELS.get(message.content_type, content_type)

    ts = message.date.astimezone(ZoneInfo("Europe/Minsk"))
    time_line = ts.strftime("%d.%m.%Y %H:%M")

    lines = [
        "<b>Обращение в поддержку</b>",
        "",
        "<b>Клиент</b>",
        f'Имя: <a href="tg://user?id={user.id}">{html.escape(_display_name(user))}</a>',
        f"Telegram: {_username_line(user)} · ID <code>{user.id}</code>",
    ]

    if content_type == "text" and message.text:
        lines.extend([
            "",
            "<b>Сообщение</b>",
            f"<blockquote>{html.escape(message.text)}</blockquote>",
        ])
    else:
        lines.extend(["", "<b>Вложение</b>", f"Тип: {html.escape(type_label)}"])
        caption = message.caption
        if caption:
            lines.append(f"Подпись: <blockquote>{html.escape(caption)}</blockquote>")

    lines.extend(["", f"<i>{time_line}</i>"])
    return "\n".join(lines)


def format_support_outbound_to_customer(*, text: str) -> str:
    return "\n".join(
        [
            "<b>Ответ поддержки PalinGames</b>",
            "",
            html.escape(text),
        ],
    )
