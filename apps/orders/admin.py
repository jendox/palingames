from django.contrib import admin
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from .models import Invoice, Order, OrderItem, PaymentEvent


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


class InvoiceInline(admin.StackedInline):
    model = Invoice
    extra = 0
    can_delete = False
    fields = (
        "provider",
        "provider_invoice_no",
        "status",
        "invoice_url",
        "amount",
        "currency",
        "paid_at",
        "cancelled_at",
        "expires_at",
        "last_status_check_at",
        "raw_create_response",
        "raw_last_status_response",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "raw_create_response",
        "raw_last_status_response",
    )


class PaymentEventInline(admin.TabularInline):
    model = PaymentEvent
    extra = 0
    can_delete = False
    fields = (
        "provider_event_key",
        "cmd_type",
        "provider_payment_no",
        "provider_status_code",
        "invoice_status",
        "amount",
        "currency",
        "is_processed",
        "processed_at",
        "created_at",
    )
    readonly_fields = fields
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


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "provider",
        "provider_invoice_no",
        "status",
        "amount",
        "currency",
        "expires_at",
        "paid_at",
        "created_at",
    )
    list_filter = ("provider", "status", "currency", "created_at", "paid_at", "cancelled_at")
    search_fields = ("provider_invoice_no", "order__email", "order__public_id")
    autocomplete_fields = ("order",)
    readonly_fields = ("created_at", "updated_at", "raw_create_response", "raw_last_status_response")
    inlines = (PaymentEventInline,)
    ordering = ("-created_at", "-id")


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "invoice",
        "provider",
        "provider_event_key",
        "cmd_type",
        "provider_payment_no",
        "provider_status_code",
        "invoice_status",
        "is_processed",
        "created_at",
    )
    list_filter = ("provider", "invoice_status", "is_processed", "currency", "created_at", "processed_at")
    search_fields = ("provider_event_key", "provider_payment_no", "provider_invoice_no", "invoice__order__email")
    autocomplete_fields = ("invoice",)
    readonly_fields = ("created_at", "updated_at", "payload", "processed_at", "processing_error")
    ordering = ("-created_at", "-id")
