from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from django.core.cache import caches

RATE_LIMIT_CACHE_ALIAS = "rate_limit"


class RateLimitScope(StrEnum):
    CHECKOUT_CREATE = "checkout:create"
    CHECKOUT_PROMO_APPLY = "checkout:promo_apply"
    AUTH_SIGNUP = "auth:signup"
    AUTH_LOGIN = "auth:login"
    AUTH_PASSWORD_RESET_REQUEST = "auth:password_reset_request"
    AUTH_PASSWORD_RESET_CONFIRM = "auth:password_reset_confirm"
    ACCOUNT_PASSWORD_CHANGE = "account:password_change"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after_seconds: int


def _hash_identifier(value: str) -> str:
    normalized = value.lower().strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _build_rate_limit_key(*, scope: RateLimitScope, identifier: str, window_seconds: int) -> str:
    identifier_hash = _hash_identifier(identifier)
    return f"rate-limit:{scope.value}:{window_seconds}:{identifier_hash}"


def _check_limit_params(
    *,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> None:
    if limit <= 0:
        msg = "Rate limit must be greater than 0"
        raise ValueError(msg)
    if window_seconds <= 0:
        msg = "Rate limit window must be greater than 0"
        raise ValueError(msg)
    if not identifier.strip():
        msg = "Rate limit identifier must not be empty"
        raise ValueError(msg)


def check_rate_limit(
    *,
    scope: RateLimitScope,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    _check_limit_params(identifier=identifier, limit=limit, window_seconds=window_seconds)
    cache = caches[RATE_LIMIT_CACHE_ALIAS]
    key = _build_rate_limit_key(
        scope=scope,
        identifier=identifier,
        window_seconds=window_seconds,
    )

    added = cache.add(key, 1, timeout=window_seconds)
    if added:
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=window_seconds)
            count = 1

    retry_after_seconds = window_seconds
    ttl = getattr(cache, "ttl", None)
    if callable(ttl):
        current_ttl = ttl(key)
        if isinstance(current_ttl, int) and current_ttl > 0:
            retry_after_seconds = current_ttl

    return RateLimitResult(
        allowed=count <= limit,
        count=count,
        limit=limit,
        retry_after_seconds=retry_after_seconds,
    )
