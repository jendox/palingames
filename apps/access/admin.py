from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import UserProductAccess


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
