from __future__ import annotations

from typing import Any, Protocol

from django.utils import timezone

from apps.access.emails import send_guest_order_download_email
from apps.custom_games.emails import (
    send_custom_game_download_email,
    send_custom_game_request_admin_email,
    send_custom_game_request_customer_email,
)
from apps.custom_games.models import CustomGameDownloadToken, CustomGameRequest
from apps.notifications.destinations import TelegramDestination
from apps.notifications.formatters import (
    format_custom_game_request_admin_telegram,
    format_review_submitted_admin_telegram,
)
from apps.notifications.telegram import send_telegram_message
from apps.orders.emails import send_order_reward_user_email
from apps.orders.models import Order
from apps.products.emails import (
    send_review_rejected_user_email,
    send_review_reward_user_email,
    send_review_submitted_admin_email,
)
from apps.products.models import Review
from apps.promocodes.models import PromoCode

from .models import NotificationOutbox
from .types import NotificationType


class NotificationHandler(Protocol):
    def __call__(self, *, outbox: NotificationOutbox, payload: Any) -> None: ...


def _send_guest_order_download_notification(*, outbox: NotificationOutbox, payload) -> None:
    send_guest_order_download_email(order=outbox.target, guest_access_payloads=payload)


def _send_custom_game_download_notification(*, outbox: NotificationOutbox, payload) -> None:
    custom_game_request = outbox.target or CustomGameRequest.objects.get(pk=payload["custom_game_request_id"])
    download_token = CustomGameDownloadToken.objects.get(pk=payload["download_token_id"])
    send_custom_game_download_email(
        custom_game_request=custom_game_request,
        download_token=download_token,
        raw_token=payload["raw_token"],
    )


def _send_order_reward_user_notification(*, outbox: NotificationOutbox, payload) -> None:
    order = outbox.target or Order.objects.select_related("reward_promo_code").get(pk=payload["order_id"])
    promo_code = order.reward_promo_code
    if promo_code is None:
        promo_code = PromoCode.objects.get(pk=payload["promo_code_id"])

    send_order_reward_user_email(order=order, promo_code=promo_code)
    Order.objects.filter(pk=order.pk, reward_email_sent_at__isnull=True).update(
        reward_email_sent_at=timezone.now(),
    )


def _send_review_rejected_user_notification(*, outbox: NotificationOutbox, payload) -> None:
    review = outbox.target or Review.objects.select_related("product", "user").get(pk=payload["review_id"])
    send_review_rejected_user_email(review=review)
    Review.objects.filter(pk=review.pk).update(rejection_notified_at=timezone.now())


def _send_review_reward_user_notification(*, outbox: NotificationOutbox, payload) -> None:
    review = outbox.target or Review.objects.select_related("reward_promo_code", "product", "user").get(
        pk=payload["review_id"],
    )
    promo_code = review.reward_promo_code
    if promo_code is None:
        promo_code = PromoCode.objects.get(pk=payload["promo_code_id"])

    send_review_reward_user_email(review=review, promo_code=promo_code)
    Review.objects.filter(pk=review.pk, reward_email_sent_at__isnull=True).update(
        reward_email_sent_at=timezone.now(),
    )


def _send_review_submitted_admin_email_notification(*, outbox: NotificationOutbox, payload) -> None:
    review = outbox.target or Review.objects.select_related("product", "user").get(pk=payload["review_id"])
    send_review_submitted_admin_email(review=review)


def _send_custom_game_request_customer_notification(*, outbox: NotificationOutbox, payload) -> None:
    custom_game_request = outbox.target or CustomGameRequest.objects.get(pk=payload["custom_game_request_id"])
    send_custom_game_request_customer_email(custom_game_request=custom_game_request)


def _send_custom_game_request_admin_email_notification(*, outbox: NotificationOutbox, payload) -> None:
    custom_game_request = outbox.target or CustomGameRequest.objects.get(pk=payload["custom_game_request_id"])
    send_custom_game_request_admin_email(custom_game_request=custom_game_request)


def _send_custom_game_request_admin_telegram_notification(*, outbox: NotificationOutbox, payload) -> None:
    custom_game_request = outbox.target or CustomGameRequest.objects.get(pk=payload["custom_game_request_id"])
    destination = TelegramDestination(payload["destination"])
    text = format_custom_game_request_admin_telegram(custom_game_request=custom_game_request)
    send_telegram_message(destination=destination, text=text)


def _send_review_submitted_admin_telegram_notification(*, outbox: NotificationOutbox, payload) -> None:
    review = outbox.target or Review.objects.select_related("product", "user").get(pk=payload["review_id"])
    destination = TelegramDestination(payload["destination"])
    text = format_review_submitted_admin_telegram(review=review)
    send_telegram_message(destination=destination, text=text)


NOTIFICATION_HANDLERS: dict[tuple[NotificationOutbox.Channel, NotificationType], NotificationHandler] = {
    (NotificationOutbox.Channel.EMAIL, NotificationType.GUEST_ORDER_DOWNLOAD):
        _send_guest_order_download_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.CUSTOM_GAME_DOWNLOAD):
        _send_custom_game_download_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.ORDER_REWARD_USER):
        _send_order_reward_user_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.REVIEW_REJECTED_USER):
        _send_review_rejected_user_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.REVIEW_REWARD_USER):
        _send_review_reward_user_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.REVIEW_SUBMITTED_ADMIN):
        _send_review_submitted_admin_email_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.CUSTOM_GAME_REQUEST_CUSTOMER):
        _send_custom_game_request_customer_notification,
    (NotificationOutbox.Channel.EMAIL, NotificationType.CUSTOM_GAME_REQUEST_ADMIN):
        _send_custom_game_request_admin_email_notification,
    (NotificationOutbox.Channel.TELEGRAM, NotificationType.CUSTOM_GAME_REQUEST_ADMIN):
        _send_custom_game_request_admin_telegram_notification,
    (NotificationOutbox.Channel.TELEGRAM, NotificationType.REVIEW_SUBMITTED_ADMIN):
        _send_review_submitted_admin_telegram_notification,
}
