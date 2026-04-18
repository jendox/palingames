from django.contrib import admin
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from apps.payments.admin import InvoiceInline

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = (
        "product",
        "title_snapshot",
        "category_snapshot",
        "unit_price_amount",
        "quantity",
        "line_total_amount",
    )
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "public_id",
        "payment_account_no",
        "status",
        "checkout_type",
        "email",
        "user",
        "items_count",
        "discount_amount",
        "total_amount",
        "currency",
        "created_at",
    )
    list_filter = (
        "status",
        "checkout_type",
        "currency",
        "created_at",
        "paid_at",
        "cancelled_at",
    )
    search_fields = (
        "id",
        "public_id",
        "payment_account_no",
        "email",
        "user__email",
        "invoice__provider_invoice_no",
        "items__title_snapshot",
    )
    autocomplete_fields = ("user",)
    readonly_fields = (
        "public_id",
        "payment_account_no",
        "subtotal_amount",
        "promo_code",
        "promo_code_snapshot",
        "discount_percent_snapshot",
        "promo_eligible_amount",
        "discount_amount",
        "total_amount",
        "currency",
        "items_count",
        "created_at",
        "updated_at",
        "paid_at",
        "cancelled_at",
        "items_preview",
    )
    inlines = (OrderItemInline, InvoiceInline)
    ordering = ("-created_at", "-id")
    save_on_top = True

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "public_id",
                    "payment_account_no",
                    "status",
                    "source",
                    "checkout_type",
                    "email",
                    "user",
                    "failure_reason",
                ),
            },
        ),
        (
            _("Суммы"),
            {
                "fields": (
                    "subtotal_amount",
                    "promo_code",
                    "promo_code_snapshot",
                    "discount_percent_snapshot",
                    "promo_eligible_amount",
                    "discount_amount",
                    "total_amount",
                    "currency",
                    "items_count",
                ),
            },
        ),
        (
            _("Позиции"),
            {
                "fields": ("items_preview",),
            },
        ),
        (
            _("Даты"),
            {
                "fields": (
                    "paid_at",
                    "cancelled_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.display(description=_("Позиции заказа"))
    def items_preview(self, obj: Order):
        items = obj.items.all()
        if not items:
            return "—"
        return format_html_join(
            "<br>",
            "{}",
            ((f"{item.title_snapshot} × {item.quantity} — {item.line_total_amount}",) for item in items),
        )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "product",
        "title_snapshot",
        "quantity",
        "unit_price_amount",
        "line_total_amount",
        "created_at",
    )
    list_filter = ("created_at", "order__status")
    search_fields = (
        "title_snapshot",
        "category_snapshot",
        "order__email",
        "product__title",
        "product__slug",
    )
    autocomplete_fields = ("order", "product")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at", "-id")
