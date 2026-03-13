class ExpressPayError(Exception):
    """Base exception for Express Pay client errors."""


class ExpressPayAPIError(ExpressPayError):
    """Raised when Express Pay returns an API-level error."""

    def __init__(self, code: int, message: str, *, msg_code: int | None = None):
        super().__init__(f"Express Pay API error {code}: {message}")
        self.code = code
        self.message = message
        self.msg_code = msg_code


class ExpressPaySignatureError(ExpressPayError):
    """Raised when webhook signature verification fails."""
