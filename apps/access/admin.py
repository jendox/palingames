from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin_site import admin_site

from .models import GuestAccess, UserProductAccess


@admin.register(UserProductAccess, site=admin_site)
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


@admin.register(GuestAccess, site=admin_site)
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
