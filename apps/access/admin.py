from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.orders.models import Order

from .models import GuestAccess, GuestAccessEmailOutbox, UserProductAccess
from .services import create_guest_access_email_outbox_for_order
from .tasks import send_guest_access_email_outbox_task


@admin.register(UserProductAccess)
class UserProductAccessAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "product",
        "order",
        "granted_at",
        "created_at",
    )
    list_filter = (
        "granted_at",
        "created_at",
        "product__currency",
        "order__status",
    )
    search_fields = (
        "user__email",
        "product__title",
        "product__slug",
        "order__payment_account_no",
        "order__email",
    )
    autocomplete_fields = ("user", "product", "order")
    readonly_fields = ("granted_at", "created_at", "updated_at")
    ordering = ("-granted_at", "-id")
    save_on_top = True

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "product",
                    "order",
                ),
            },
        ),
        (
            _("Даты"),
            {
                "fields": (
                    "granted_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )


@admin.register(GuestAccess)
class GuestAccessAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "email",
        "product",
        "order",
        "expires_at",
        "downloads_count",
        "max_downloads",
        "last_used_at",
        "revoked_at",
        "created_at",
    )
    list_filter = (
        "expires_at",
        "last_used_at",
        "revoked_at",
        "created_at",
    )
    search_fields = (
        "email",
        "product__title",
        "product__slug",
        "order__payment_account_no",
        "order__email",
    )
    autocomplete_fields = ("product", "order")
    readonly_fields = ("token_hash", "created_at", "updated_at")
    ordering = ("-created_at", "-id")
    save_on_top = True

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "order",
                    "product",
                    "email",
                    "token_hash",
                ),
            },
        ),
        (
            _("Статус"),
            {
                "fields": (
                    "expires_at",
                    "downloads_count",
                    "max_downloads",
                    "last_used_at",
                    "revoked_at",
                ),
            },
        ),
        (
            _("Даты"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )


@admin.register(GuestAccessEmailOutbox)
class GuestAccessEmailOutboxAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "email",
        "order",
        "status",
        "attempts",
        "last_attempt_at",
        "sent_at",
        "created_at",
    )
    list_filter = (
        "status",
        "last_attempt_at",
        "sent_at",
        "created_at",
    )
    search_fields = (
        "email",
        "order__payment_account_no",
        "order__email",
    )
    autocomplete_fields = ("order",)
    actions = ("resend_selected_emails", "retry_failed_selected_emails")
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
                    "order",
                    "email",
                    "status",
                    "attempts",
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
    def resend_selected_emails(self, request, queryset):
        queued = 0
        skipped = 0

        for outbox in queryset.select_related("order"):
            order = outbox.order
            if order.checkout_type != Order.CheckoutType.GUEST or order.status != Order.OrderStatus.PAID:
                skipped += 1
                continue

            new_outbox = create_guest_access_email_outbox_for_order(order)
            if new_outbox is None:
                skipped += 1
                continue

            send_guest_access_email_outbox_task.delay(new_outbox.id)
            queued += 1

        if queued:
            self.message_user(
                request,
                f"Поставлено в очередь писем: {queued}.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Пропущено записей: {skipped}.",
                level=messages.WARNING,
            )

    @admin.action(description="Повторить отправку для failed outbox")
    def retry_failed_selected_emails(self, request, queryset):
        queued = 0
        skipped = 0

        for outbox in queryset:
            if outbox.status != GuestAccessEmailOutbox.GuestAccessEmailStatus.FAILED:
                skipped += 1
                continue
            send_guest_access_email_outbox_task.delay(outbox.id)
            queued += 1

        if queued:
            self.message_user(
                request,
                f"Повторно поставлено в очередь писем: {queued}.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Пропущено записей: {skipped}.",
                level=messages.WARNING,
            )
