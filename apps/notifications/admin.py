from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.admin_site import admin_site
from apps.orders.models import Order

from .models import NotificationOutbox
from .services import enqueue_notification_outbox
from .tasks import send_notification_outbox_task
from .types import GUEST_ORDER_DOWNLOAD


@admin.register(NotificationOutbox, site=admin_site)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "notification_type",
        "channel",
        "recipient",
        "status",
        "attempts",
        "last_attempt_at",
        "sent_at",
        "created_at",
    )
    list_filter = (
        "notification_type",
        "channel",
        "status",
        "last_attempt_at",
        "sent_at",
        "created_at",
    )
    search_fields = ("recipient", "notification_type", "last_error")
    actions = ("resend_guest_download_emails", "retry_failed_notifications")
    readonly_fields = (
        "payload_encrypted",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at", "-id")
    save_on_top = True

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "notification_type",
                    "channel",
                    "recipient",
                    "status",
                    "attempts",
                ),
            },
        ),
        (
            _("Связанный объект"),
            {
                "fields": (
                    "content_type",
                    "object_id",
                ),
            },
        ),
        (
            _("Содержимое"),
            {
                "fields": (
                    "payload_encrypted",
                    "last_error",
                ),
            },
        ),
        (
            _("Даты"),
            {
                "fields": (
                    "last_attempt_at",
                    "sent_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.action(description="Повторно отправить письма с новыми ссылками")
    def resend_guest_download_emails(self, request, queryset):
        from apps.access.services import create_guest_access_notification_for_order

        queued = 0
        skipped = 0
        for outbox in queryset:
            if outbox.notification_type != GUEST_ORDER_DOWNLOAD:
                skipped += 1
                continue
            order = outbox.target
            if not isinstance(order, Order) or order.checkout_type != Order.CheckoutType.GUEST:
                skipped += 1
                continue
            if order.status != Order.OrderStatus.PAID:
                skipped += 1
                continue

            new_outbox = create_guest_access_notification_for_order(order)
            if new_outbox is None:
                skipped += 1
                continue
            enqueue_notification_outbox(new_outbox)
            queued += 1

        self._message_action_result(request, queued=queued, skipped=skipped)

    @admin.action(description="Повторить отправку для failed уведомлений")
    def retry_failed_notifications(self, request, queryset):
        queued = 0
        skipped = 0
        for outbox in queryset:
            if outbox.status != NotificationOutbox.Status.FAILED:
                skipped += 1
                continue
            send_notification_outbox_task.delay(outbox.id)
            queued += 1

        self._message_action_result(request, queued=queued, skipped=skipped)

    def _message_action_result(self, request, *, queued: int, skipped: int) -> None:
        if queued:
            self.message_user(request, f"Поставлено в очередь уведомлений: {queued}.", level=messages.SUCCESS)
        if skipped:
            self.message_user(request, f"Пропущено записей: {skipped}.", level=messages.WARNING)
