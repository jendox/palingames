from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.logging import log_event
from apps.core.metrics import inc_auth_rate_limit_triggered
from apps.core.rate_limits import RateLimitResult, RateLimitScope, check_rate_limit

AUTH_LOGIN_PATH = "/_allauth/browser/v1/auth/login"
AUTH_LOGIN_RATE_LIMIT_MESSAGE = "Слишком много попыток входа. Попробуйте позже."
AUTH_SIGNUP_PATH = "/_allauth/browser/v1/auth/signup"
AUTH_SIGNUP_RATE_LIMIT_MESSAGE = "Слишком много попыток регистрации. Попробуйте позже."
AUTH_PASSWORD_RESET_REQUEST_PATH = "/_allauth/browser/v1/auth/password/request"
AUTH_PASSWORD_RESET_REQUEST_RATE_LIMIT_MESSAGE = "Слишком много запросов сброса пароля. Попробуйте позже."
AUTH_PASSWORD_RESET_CONFIRM_PATH = "/_allauth/browser/v1/auth/password/reset"
AUTH_PASSWORD_RESET_CONFIRM_RATE_LIMIT_MESSAGE = "Слишком много попыток сброса пароля. Попробуйте позже."
logger = logging.getLogger("apps.auth.rate_limits")


@dataclass(frozen=True)
class AuthRateLimitConfig:
    checker: Callable[[HttpRequest], tuple[RateLimitResult | None, str | None]]
    message: str
    scope: str


def _get_client_ip(request: HttpRequest) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return str(forwarded_for).split(",", maxsplit=1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _get_json_body(request: HttpRequest) -> dict:
    try:
        payload = json.loads(request.body.decode(request.encoding or "utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _get_auth_email(request: HttpRequest) -> str:
    payload = _get_json_body(request)
    email = payload.get("email") or payload.get("login")
    return str(email).strip().lower() if email else ""


def _get_auth_key(request: HttpRequest) -> str:
    payload = _get_json_body(request)
    key = payload.get("key")
    return str(key).strip() if key else ""


def _rate_limited_response(*, message: str, retry_after_seconds: int) -> JsonResponse:
    response = JsonResponse(
        {
            "errors": [
                {
                    "message": message,
                    "code": "rate_limited",
                },
            ],
        },
        status=429,
    )
    response["Retry-After"] = str(retry_after_seconds)
    return response


def _check_auth_login_rate_limit(request: HttpRequest):
    email = _get_auth_email(request)
    if email:
        email_result = check_rate_limit(
            scope=RateLimitScope.AUTH_LOGIN,
            identifier=f"email:{email}",
            limit=settings.AUTH_LOGIN_EMAIL_RATE_LIMIT,
            window_seconds=settings.AUTH_LOGIN_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not email_result.allowed:
            return email_result, "email"

    ip = _get_client_ip(request)
    if not ip:
        return None, None

    return (
        check_rate_limit(
        scope=RateLimitScope.AUTH_LOGIN,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_LOGIN_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS,
        ),
        "ip",
    )


def _check_auth_signup_rate_limit(request: HttpRequest):
    email = _get_auth_email(request)
    if email:
        email_result = check_rate_limit(
            scope=RateLimitScope.AUTH_SIGNUP,
            identifier=f"email:{email}",
            limit=settings.AUTH_SIGNUP_EMAIL_RATE_LIMIT,
            window_seconds=settings.AUTH_SIGNUP_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not email_result.allowed:
            return email_result, "email"

    ip = _get_client_ip(request)
    if not ip:
        return None, None

    return (
        check_rate_limit(
        scope=RateLimitScope.AUTH_SIGNUP,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_SIGNUP_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS,
        ),
        "ip",
    )


def _check_auth_password_reset_request_rate_limit(request: HttpRequest):
    email = _get_auth_email(request)
    if email:
        email_result = check_rate_limit(
            scope=RateLimitScope.AUTH_PASSWORD_RESET_REQUEST,
            identifier=f"email:{email}",
            limit=settings.AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT,
            window_seconds=settings.AUTH_PASSWORD_RESET_REQUEST_EMAIL_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not email_result.allowed:
            return email_result, "email"

    ip = _get_client_ip(request)
    if not ip:
        return None, None

    return (
        check_rate_limit(
        scope=RateLimitScope.AUTH_PASSWORD_RESET_REQUEST,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_PASSWORD_RESET_REQUEST_IP_RATE_LIMIT_WINDOW_SECONDS,
        ),
        "ip",
    )


def _check_auth_password_reset_confirm_rate_limit(request: HttpRequest):
    key = _get_auth_key(request)
    if key:
        key_result = check_rate_limit(
            scope=RateLimitScope.AUTH_PASSWORD_RESET_CONFIRM,
            identifier=f"key:{key}",
            limit=settings.AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT,
            window_seconds=settings.AUTH_PASSWORD_RESET_CONFIRM_KEY_RATE_LIMIT_WINDOW_SECONDS,
        )
        if not key_result.allowed:
            return key_result, "key"

    ip = _get_client_ip(request)
    if not ip:
        return None, None

    return (
        check_rate_limit(
        scope=RateLimitScope.AUTH_PASSWORD_RESET_CONFIRM,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_PASSWORD_RESET_CONFIRM_IP_RATE_LIMIT_WINDOW_SECONDS,
        ),
        "ip",
    )


AUTH_RATE_LIMIT_CONFIGS = {
    AUTH_LOGIN_PATH: AuthRateLimitConfig(
        checker=_check_auth_login_rate_limit,
        message=AUTH_LOGIN_RATE_LIMIT_MESSAGE,
        scope=RateLimitScope.AUTH_LOGIN,
    ),
    AUTH_SIGNUP_PATH: AuthRateLimitConfig(
        checker=_check_auth_signup_rate_limit,
        message=AUTH_SIGNUP_RATE_LIMIT_MESSAGE,
        scope=RateLimitScope.AUTH_SIGNUP,
    ),
    AUTH_PASSWORD_RESET_REQUEST_PATH: AuthRateLimitConfig(
        checker=_check_auth_password_reset_request_rate_limit,
        message=AUTH_PASSWORD_RESET_REQUEST_RATE_LIMIT_MESSAGE,
        scope=RateLimitScope.AUTH_PASSWORD_RESET_REQUEST,
    ),
    AUTH_PASSWORD_RESET_CONFIRM_PATH: AuthRateLimitConfig(
        checker=_check_auth_password_reset_confirm_rate_limit,
        message=AUTH_PASSWORD_RESET_CONFIRM_RATE_LIMIT_MESSAGE,
        scope=RateLimitScope.AUTH_PASSWORD_RESET_CONFIRM,
    ),
}


class AuthRateLimitMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method != "POST":
            return self.get_response(request)

        config = AUTH_RATE_LIMIT_CONFIGS.get(request.path)
        if config is None:
            return self.get_response(request)

        rate_limit, identifier_type = config.checker(request)
        if rate_limit is not None and not rate_limit.allowed:
            inc_auth_rate_limit_triggered(
                scope=config.scope,
                identifier_type=identifier_type or "unknown",
            )
            log_event(
                logger,
                logging.WARNING,
                "auth.rate_limit.triggered",
                path=request.path,
                scope=config.scope,
                identifier_type=identifier_type,
                retry_after_seconds=rate_limit.retry_after_seconds,
            )
            return _rate_limited_response(
                message=config.message,
                retry_after_seconds=rate_limit.retry_after_seconds,
            )
        return self.get_response(request)
