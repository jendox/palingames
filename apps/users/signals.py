import logging

from allauth.account.models import EmailAddress
from allauth.account.signals import (
    authentication_step_completed,
    email_confirmed,
    password_changed,
    password_reset,
    password_set,
    user_signed_up,
)
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from apps.core.logging import log_event
from apps.core.metrics import (
    inc_auth_login_failed,
    inc_auth_login_success,
    inc_auth_password_reset_completed,
    inc_auth_signup_success,
)
from apps.orders.guest_merge import merge_guest_orders_for_user

logger = logging.getLogger("apps.users.signals")
AUTHENTICATION_METHODS_SESSION_KEY = "account_authentication_methods"


def _get_latest_auth_method(request) -> dict:
    if request is None or not hasattr(request, "session"):
        return {}
    methods = request.session.get(AUTHENTICATION_METHODS_SESSION_KEY, [])
    if not isinstance(methods, list) or not methods:
        return {}
    latest = methods[-1]
    return latest if isinstance(latest, dict) else {}


def _merge_guest_orders_for_verified_email(*, user, email: str, event: str) -> None:
    merged_orders_count = merge_guest_orders_for_user(
        user=user,
        email=email,
    )
    log_event(
        logger,
        logging.INFO,
        event,
        user_id=user.id,
        email=email,
        merged_orders_count=merged_orders_count,
    )


@receiver(authentication_step_completed)
def log_authentication_step_completed(request, user, method, **kwargs):
    event = "auth.login.authentication_step.completed"
    if method == "socialaccount":
        event = "auth.social.login.authentication_step.completed"
    log_event(
        logger,
        logging.INFO,
        event,
        user_id=user.id,
        method=method,
        provider=kwargs.get("provider"),
    )


@receiver(user_logged_in)
def log_auth_login_succeeded(sender, request, user, **kwargs):
    latest_method = _get_latest_auth_method(request)
    method = latest_method.get("method") or "unknown"
    provider = latest_method.get("provider")
    inc_auth_login_success(method=method)
    event = "auth.social.login.succeeded" if method == "socialaccount" else "auth.login.succeeded"
    log_event(
        logger,
        logging.INFO,
        event,
        user_id=user.id,
        method=method,
        provider=provider,
    )


@receiver(user_login_failed)
def log_auth_login_failed(sender, credentials, request, **kwargs):
    login_field = "unknown"
    if isinstance(credentials, dict):
        if credentials.get("email"):
            login_field = "email"
        elif credentials.get("login"):
            login_field = "login"
        elif credentials.get("username"):
            login_field = "username"
    inc_auth_login_failed(login_field=login_field)
    log_event(
        logger,
        logging.WARNING,
        "auth.login.failed",
        reason="invalid_credentials",
        login_field=login_field,
        request_path=getattr(request, "path", None),
    )


@receiver(user_signed_up)
def log_auth_signup_succeeded(request, user, **kwargs):
    sociallogin = kwargs.get("sociallogin")
    method = "socialaccount" if sociallogin else "password"
    inc_auth_signup_success(method=method)
    log_event(
        logger,
        logging.INFO,
        "auth.signup.succeeded",
        user_id=user.id,
        method=method,
        provider=getattr(getattr(sociallogin, "account", None), "provider", None),
    )


@receiver(password_reset)
def log_auth_password_reset_completed(request, user, **kwargs):
    inc_auth_password_reset_completed()
    log_event(
        logger,
        logging.INFO,
        "auth.password_reset.completed",
        user_id=user.id,
    )


@receiver(password_changed)
def log_auth_password_changed(request, user, **kwargs):
    log_event(
        logger,
        logging.INFO,
        "auth.password_changed",
        user_id=user.id,
    )


@receiver(password_set)
def log_auth_password_set(request, user, **kwargs):
    log_event(
        logger,
        logging.INFO,
        "auth.password_set",
        user_id=user.id,
    )


@receiver(email_confirmed)
def merge_guest_orders_after_email_confirmation(request, email_address, **kwargs):
    _merge_guest_orders_for_verified_email(
        user=email_address.user,
        email=email_address.email,
        event="users.email_confirmed.guest_orders_merged",
    )


@receiver(user_logged_in)
def merge_guest_orders_after_login(sender, request, user, **kwargs):
    verified_email = (
        EmailAddress.objects.filter(user=user, verified=True)
        .order_by("-primary", "-id")
        .values_list("email", flat=True)
        .first()
    )
    if not verified_email:
        return

    _merge_guest_orders_for_verified_email(
        user=user,
        email=verified_email,
        event="users.login.guest_orders_merged",
    )
