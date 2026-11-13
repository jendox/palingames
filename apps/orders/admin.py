from decimal import Decimal

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.access.services import GuestAccessGrantError, grant_guest_access_and_notify
from apps.core.admin_site import admin_site
from apps.core.models import OrderSource
from apps.orders.forms import OrderAdminForm
from apps.payments.admin import InvoiceInline
from apps.payments.jobs import enqueue_invoice_creation
from apps.payments.models import Invoice

from .models import Order, OrderItem
from .services import recalculate_manual_order_from_items
from .watchdog import WATCHDOG_GRACE_PERIOD

_OFFSITE_ORDER_SOURCES = frozenset({OrderSource.TELEGRAM, OrderSource.INSTAGRAM})


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    can_delete = True
    autocomplete_fields = ("product",)
    show_change_link = True
    fields = (
        "product",
        "quantity",
        "title_snapshot",
        "category_snapshot",
        "unit_price_amount",
        "line_total_amount",
        "promo_eligible",
        "discount_amount",
        "discounted_line_total_amount",
    )
    _computed_readonly = (
        "title_snapshot",
        "category_snapshot",
        "unit_price_amount",
        "line_total_amount",
        "promo_eligible",
        "discount_amount",
        "discounted_line_total_amount",
    )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != Order.OrderStatus.CREATED:
            return self.fields
        return self._computed_readonly

    def has_add_permission(self, request, obj=None):
        if obj is not None and obj.status != Order.OrderStatus.CREATED:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status != Order.OrderStatus.CREATED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Order, site=admin_site)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = (
        "id",
        "public_id",
        "payment_account_no",
        "status",
        "source",
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
        "source",
        "checkout_type",
        "currency",
        "created_at",
        "paid_at",
        "refunded_at",
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
    autocomplete_fields = ("user", "promo_code")
    _base_readonly_fields = (
        "public_id",
        "payment_account_no",
        "subtotal_amount",
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
        "refunded_at",
        "cancelled_at",
        "items_preview",
        "admin_order_actions",
    )
    readonly_fields = _base_readonly_fields
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
                    "admin_order_actions",
                    "status",
                    "source",
                    "checkout_type",
                    "email",
                    "admin_email_preset",
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
                    "refunded_at",
                    "cancelled_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        ro = list(self._base_readonly_fields)
        if obj is not None and obj.status != Order.OrderStatus.CREATED:
            ro.append("promo_code")
        return ro

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        emails = [str(e).strip() for e in getattr(settings, "CUSTOM_GAME_ADMIN_EMAILS", []) if str(e).strip()]
        if emails:
            initial.setdefault("email", emails[0])
        return initial

    def get_urls(self):
        custom_urls = [
            path(
                "<int:object_id>/create-invoice/",
                self.admin_site.admin_view(self.create_invoice_view),
                name="orders_order_create_invoice",
            ),
            path(
                "<int:object_id>/grant-guest-access/",
                self.admin_site.admin_view(self.grant_guest_access_view),
                name="orders_order_grant_guest_access",
            ),
        ]
        return custom_urls + super().get_urls()

    @staticmethod
    def _should_recalculate_order(order: Order) -> bool:
        return order.status == Order.OrderStatus.CREATED

    @admin.display(description=_("Действия"))
    def admin_order_actions(self, obj: Order):
        if not obj.pk:
            return _("Сохраните заказ перед созданием инвойса.")

        actions = []
        if self._can_create_invoice_from_admin(obj):
            url = reverse("admin:orders_order_create_invoice", args=[obj.pk])
            actions.append(format_html('<a class="button" href="{}">{}</a>', url, _("Создать инвойс")))
        if self._can_grant_guest_access_from_admin(obj):
            url = reverse("admin:orders_order_grant_guest_access", args=[obj.pk])
            actions.append(
                format_html(
                    '<a class="button" href="{}">{}</a>',
                    url,
                    _("Выдать гостевой доступ и отправить письмо"),
                ),
            )
        if actions:
            return format_html_join(" ", "{}", ((action,) for action in actions))
        return "—"

    @staticmethod
    def _can_create_invoice_from_admin(obj: Order) -> bool:
        if obj.status != Order.OrderStatus.CREATED:
            return False
        if obj.total_amount <= Decimal("0.00"):
            return False
        if not obj.items.exists():
            return False
        invoice = Invoice.objects.filter(order=obj).first()
        if invoice and invoice.status in {Invoice.InvoiceStatus.PENDING, Invoice.InvoiceStatus.PAID}:
            return False
        if obj.source in _OFFSITE_ORDER_SOURCES:
            return True
        return obj.created_at <= timezone.now() - WATCHDOG_GRACE_PERIOD

    @staticmethod
    def _can_grant_guest_access_from_admin(obj: Order) -> bool:
        if obj.status != Order.OrderStatus.PAID:
            return False
        if obj.checkout_type != Order.CheckoutType.GUEST:
            return False
        if not obj.items.exists():
            return False
        return bool((obj.email or "").strip())

    def create_invoice_view(self, request, object_id: int):
        order = self.get_object(request, object_id)
        if order is None:
            self.message_user(request, _("Заказ не найден."), messages.ERROR)
            return HttpResponseRedirect(reverse("admin:orders_order_changelist"))
        if not self.has_change_permission(request, order):
            raise PermissionDenied

        if not self._can_create_invoice_from_admin(order):
            self.message_user(
                request,
                _("Создание инвойса недоступно для этого заказа."),
                messages.ERROR,
            )
        else:
            enqueue_invoice_creation(order.id, payment_target="order")
            self.message_user(request, _("Создание инвойса поставлено в очередь."), messages.SUCCESS)

        return HttpResponseRedirect(reverse("admin:orders_order_change", args=[order.pk]))

    def _order_admin_change_url(self, order: Order | None) -> str:
        if order is None:
            return reverse("admin:orders_order_changelist")
        return reverse("admin:orders_order_change", args=[order.pk])

    def _redirect_grant_guest_access_error(self, request, redirect_url: str, message: str):
        self.message_user(request, message, messages.ERROR)
        return HttpResponseRedirect(redirect_url)

    def _grant_guest_access_confirm_context(self, request, order: Order, *, cancel_url: str) -> dict:
        return {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "order": order,
            "items": order.items.select_related("product").all(),
            "title": _("Выдать гостевой доступ и отправить письмо"),
            "cancel_url": cancel_url,
        }

    def grant_guest_access_view(self, request, object_id: int):
        order = self.get_object(request, object_id)
        change_url = self._order_admin_change_url(order)
        if order is None:
            return self._redirect_grant_guest_access_error(request, change_url, _("Заказ не найден."))
        if not self.has_change_permission(request, order):
            raise PermissionDenied

        unavailable_message = _("Выдача гостевого доступа недоступна для этого заказа.")
        if not self._can_grant_guest_access_from_admin(order):
            return self._redirect_grant_guest_access_error(request, change_url, unavailable_message)

        if request.method == "GET":
            context = self._grant_guest_access_confirm_context(request, order, cancel_url=change_url)
            return TemplateResponse(
                request,
                "admin/orders/order/grant_guest_access_confirm.html",
                context,
            )

        try:
            outbox = grant_guest_access_and_notify(order, requested_by=request.user)
        except GuestAccessGrantError as exc:
            return self._redirect_grant_guest_access_error(request, change_url, str(exc))

        if outbox is None:
            return self._redirect_grant_guest_access_error(
                request,
                change_url,
                _("Не удалось выдать доступ: проверьте позиции заказа."),
            )

        self.message_user(
            request,
            _("Гостевой доступ выдан, письмо поставлено в очередь."),
            messages.SUCCESS,
        )
        return HttpResponseRedirect(change_url)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.subtotal_amount = obj.subtotal_amount or Decimal("0.00")
            obj.total_amount = obj.total_amount or Decimal("0.00")
            if obj.items_count is None:
                obj.items_count = 0
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        order = form.instance
        if order.pk and self._should_recalculate_order(order):
            try:
                recalculate_manual_order_from_items(order_id=order.pk)
            except ValidationError as exc:
                if getattr(exc, "message_dict", None):
                    text = "; ".join(f"{k}: {v}" for k, v in exc.message_dict.items())
                elif getattr(exc, "messages", None):
                    text = "; ".join(str(m) for m in exc.messages)
                else:
                    text = str(exc)
                self.message_user(request, text, level=messages.ERROR)

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


@admin.register(OrderItem, site=admin_site)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "product",
        "title_snapshot",
        "quantity",
        "unit_price_amount",
        "line_total_amount",
        "discount_amount",
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
