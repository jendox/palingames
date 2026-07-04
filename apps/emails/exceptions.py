from __future__ import annotations


class EmailDeliveryError(Exception):
    """Base error for outbound email delivery."""


class EmailSuppressedError(EmailDeliveryError):
    def __init__(self, *, email: str, reason: str) -> None:
        super().__init__(f"Email suppressed: {email} ({reason})")
        self.email = email
        self.reason = reason
