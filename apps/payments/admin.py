from django.contrib import admin

from .models import Invoice, PaymentEvent


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
