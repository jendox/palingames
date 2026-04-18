from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.core.rate_limits import RateLimitScope, check_rate_limit

AUTH_LOGIN_PATH = "/_allauth/browser/v1/auth/login"
AUTH_LOGIN_RATE_LIMIT_MESSAGE = "Слишком много попыток входа. Попробуйте позже."
AUTH_SIGNUP_PATH = "/_allauth/browser/v1/auth/signup"
AUTH_SIGNUP_RATE_LIMIT_MESSAGE = "Слишком много попыток регистрации. Попробуйте позже."


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
            return email_result

    ip = _get_client_ip(request)
    if not ip:
        return None

    return check_rate_limit(
        scope=RateLimitScope.AUTH_LOGIN,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_LOGIN_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_LOGIN_IP_RATE_LIMIT_WINDOW_SECONDS,
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
            return email_result

    ip = _get_client_ip(request)
    if not ip:
        return None

    return check_rate_limit(
        scope=RateLimitScope.AUTH_SIGNUP,
        identifier=f"ip:{ip}",
        limit=settings.AUTH_SIGNUP_IP_RATE_LIMIT,
        window_seconds=settings.AUTH_SIGNUP_IP_RATE_LIMIT_WINDOW_SECONDS,
    )


class AuthRateLimitMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method != "POST":
            return self.get_response(request)

        if request.path == AUTH_LOGIN_PATH:
            rate_limit = _check_auth_login_rate_limit(request)
            if rate_limit is not None and not rate_limit.allowed:
                return _rate_limited_response(
                    message=AUTH_LOGIN_RATE_LIMIT_MESSAGE,
                    retry_after_seconds=rate_limit.retry_after_seconds,
                )

        if request.path == AUTH_SIGNUP_PATH:
            rate_limit = _check_auth_signup_rate_limit(request)
            if rate_limit is not None and not rate_limit.allowed:
                return _rate_limited_response(
                    message=AUTH_SIGNUP_RATE_LIMIT_MESSAGE,
                    retry_after_seconds=rate_limit.retry_after_seconds,
                )

        return self.get_response(request)
