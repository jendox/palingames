from __future__ import annotations

import secrets

MANAGED_LINK_TOKEN_BYTES = 16
MANAGED_LINK_TOKEN_PREFIX_LENGTH = 8


def generate_managed_link_token() -> str:
    return secrets.token_urlsafe(MANAGED_LINK_TOKEN_BYTES)


def managed_link_token_prefix(token: str) -> str:
    return token[:MANAGED_LINK_TOKEN_PREFIX_LENGTH]
