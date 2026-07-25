from __future__ import annotations

from dataclasses import dataclass

from bot.telegram_bot.telegram.errors import TelegramConfigurationError


@dataclass(frozen=True)
class OutboundMessage:
    source: str
    destination: str
    text: str
    parse_mode: str
    correlation_id: str


def parse_outbound_fields(fields: dict[str, str]) -> OutboundMessage:
    destination = (fields.get("destination") or "").strip()
    text = fields.get("text") or ""
    if not destination:
        raise TelegramConfigurationError("Outbound message is missing destination")
    if not text:
        raise TelegramConfigurationError("Outbound message is missing text")

    return OutboundMessage(
        source=(fields.get("source") or "").strip(),
        destination=destination,
        text=text,
        parse_mode=(fields.get("parse_mode") or "HTML").strip() or "HTML",
        correlation_id=(fields.get("correlation_id") or "").strip(),
    )
