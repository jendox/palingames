from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin_site import admin_site

from .models import EmailLog, EmailSuppression


@admin.register(EmailLog, site=admin_site)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "subject",
        "notification_type",
        "status",
        "provider",
        "sent_at",
        "created_at",
    )
    list_filter = ("status", "provider", "notification_type", "created_at", "sent_at")
    search_fields = ("recipient", "subject", "notification_type", "message_id", "error")
    readonly_fields = (
        "notification_outbox",
        "recipient",
        "subject",
        "template_key",
        "notification_type",
        "status",
        "provider",
        "message_id",
        "smtp_response",
        "error",
        "sent_at",
        "metadata",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at", "-id")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "recipient",
                    "subject",
                    "notification_type",
                    "template_key",
                    "status",
                    "provider",
                ),
            },
        ),
        (
            _("Связи"),
            {"fields": ("notification_outbox",)},
        ),
        (
            _("Доставка"),
            {
                "fields": (
                    "message_id",
                    "smtp_response",
                    "error",
                    "sent_at",
                    "metadata",
                ),
            },
        ),
        (
            _("Даты"),
            {"fields": ("created_at", "updated_at")},
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailSuppression, site=admin_site)
class EmailSuppressionAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "reason", "active", "source", "suppressed_at", "created_at")
    list_filter = ("active", "reason", "source", "suppressed_at", "created_at")
    search_fields = ("email", "source")
    readonly_fields = ("suppressed_at", "created_at", "updated_at")
    ordering = ("-suppressed_at", "-id")
    list_editable = ("active",)  # быстро снять suppression в списке

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "email",
                    "reason",
                    "active",
                    "source",
                ),
            },
        ),
        (
            _("Детали"),
            {"fields": ("details", "suppressed_at", "created_at", "updated_at")},
        ),
    )
