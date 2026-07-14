from __future__ import annotations


class TelegramConfigurationError(RuntimeError):
    pass


class TelegramDeliveryError(RuntimeError):
    pass


class TransientTelegramDeliveryError(TelegramDeliveryError):
    pass


class PermanentTelegramDeliveryError(TelegramDeliveryError):
    pass
