from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from .models import PromoCode, PromoCodeRedemption


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "discount_percent",
        "is_active",
        "min_order_amount",
        "max_total_redemptions",
        "redemptions_count",
        "starts_at",
        "ends_at",
    )
    list_filter = ("is_active", "starts_at", "ends_at", "created_at")
    search_fields = ("code", "assigned_email", "assigned_user__email", "note")
    autocomplete_fields = ("assigned_user",)
    filter_horizontal = ("categories", "products")
    readonly_fields = ("created_at", "updated_at", "redemptions_count")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "code",
                    "discount_percent",
                    "is_active",
                    "starts_at",
                    "ends_at",
                    "note",
                ),
            },
        ),
        (_("Ограничения"), {"fields": ("min_order_amount", "categories", "products")}),
        (
            _("Лимиты"),
            {
                "fields": (
                    "max_total_redemptions",
                    "max_redemptions_per_user",
                    "max_redemptions_per_email",
                    "assigned_user",
                    "assigned_email",
                ),
            },
        ),
        (_("Статистика"), {"fields": ("redemptions_count", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_redemptions_count=Count("redemptions"))

    @admin.display(description=_("Использований"), ordering="_redemptions_count")
    def redemptions_count(self, obj):
        return getattr(obj, "_redemptions_count", obj.redemptions.count())


@admin.register(PromoCodeRedemption)
class PromoCodeRedemptionAdmin(admin.ModelAdmin):
    list_display = (
        "promo_code",
        "order",
        "user",
        "email",
        "eligible_amount",
        "discount_amount",
        "created_at",
    )
    list_filter = ("created_at", "promo_code")
    search_fields = ("promo_code__code", "order__payment_account_no", "order__email", "email", "user__email")
    autocomplete_fields = ("promo_code", "order", "user")
    readonly_fields = (
        "promo_code",
        "order",
        "user",
        "email",
        "subtotal_amount",
        "eligible_amount",
        "discount_amount",
        "created_at",
        "updated_at",
    )
