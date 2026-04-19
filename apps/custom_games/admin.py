from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.custom_games.forms import CustomGameFileAdminForm
from apps.custom_games.models import CustomGameDownloadToken, CustomGameFile, CustomGameRequest
from apps.custom_games.services import send_custom_game_download_link
from apps.payments.jobs import enqueue_invoice_creation
from apps.payments.models import Invoice
from apps.products.services.s3 import delete_product_file, upload_custom_game_file


class CustomGameInvoiceInline(admin.StackedInline):
    model = Invoice
    fk_name = "custom_game_request"
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
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomGameRequest)
class CustomGameRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "payment_account_no",
        "status",
        "contact_name",
        "contact_email",
        "quoted_price",
        "currency",
        "deadline",
        "created_at",
    )
    list_filter = ("status", "source", "currency", "created_at", "deadline")
    search_fields = (
        "id",
        "public_id",
        "payment_account_no",
        "contact_name",
        "contact_email",
        "contact_phone",
        "idea",
    )
    autocomplete_fields = ("user",)
    readonly_fields = (
        "public_id",
        "payment_account_no",
        "created_at",
        "updated_at",
        "deadline_reminder_sent_at",
        "delivered_at",
        "cancelled_at",
        "admin_actions",
        "files_summary",
    )
    inlines = (CustomGameInvoiceInline,)
    actions = ("mark_in_progress", "mark_ready")
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
                    "user",
                    "contact_name",
                    "contact_email",
                    "contact_phone",
                    "admin_actions",
                ),
            },
        ),
        (
            _("Заявка"),
            {
                "fields": (
                    "idea",
                    "audience",
                    "timing",
                ),
            },
        ),
        (
            _("Работа и оплата"),
            {
                "fields": (
                    "quoted_price",
                    "currency",
                    "deadline",
                    "admin_notes",
                ),
            },
        ),
        (
            _("Файлы"),
            {
                "fields": ("files_summary",),
            },
        ),
        (
            _("Служебные даты"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "deadline_reminder_sent_at",
                    "delivered_at",
                    "cancelled_at",
                ),
            },
        ),
    )

    def get_urls(self):
        custom_urls = [
            path(
                "<int:object_id>/create-invoice/",
                self.admin_site.admin_view(self.create_invoice_view),
                name="custom_games_customgamerequest_create_invoice",
            ),
            path(
                "<int:object_id>/resend-download-email/",
                self.admin_site.admin_view(self.resend_download_email_view),
                name="custom_games_customgamerequest_resend_download_email",
            ),
        ]
        return custom_urls + super().get_urls()

    @admin.display(description=_("Действия"))
    def admin_actions(self, obj):
        if not obj.pk:
            return "Сохраните заявку перед выполнением действий."

        buttons = []
        if self._can_show_create_invoice_button(obj):
            buttons.append(
                (
                    reverse("admin:custom_games_customgamerequest_create_invoice", args=[obj.pk]),
                    _("Создать инвойс"),
                ),
            )
        if self._can_show_resend_download_email_button(obj):
            buttons.append(
                (
                    reverse("admin:custom_games_customgamerequest_resend_download_email", args=[obj.pk]),
                    _("Отправить повторно имейл со ссылкой на скачивание"),
                ),
            )

        if not buttons:
            return "Нет доступных действий."

        return format_html_join(
            " ",
            '<a class="button" href="{}">{}</a>',
            buttons,
        )

    def _can_show_create_invoice_button(self, obj) -> bool:
        return (
            obj.status == CustomGameRequest.Status.READY
            and not Invoice.objects.filter(
                custom_game_request=obj,
                status=Invoice.InvoiceStatus.PENDING,
            ).exists()
        )

    def _can_show_resend_download_email_button(self, obj) -> bool:
        return obj.status == CustomGameRequest.Status.DELIVERED and obj.files.filter(is_active=True).exists()

    def create_invoice_view(self, request, object_id: int):
        custom_game_request = self.get_object(request, object_id)
        if custom_game_request is None:
            self.message_user(request, "Заявка не найдена.", messages.ERROR)
            return self._redirect_to_changelist()
        if not self.has_change_permission(request, custom_game_request):
            raise PermissionDenied

        if not self._can_show_create_invoice_button(custom_game_request):
            self.message_user(
                request,
                "Инвойс можно создать только для готовой заявки без инвойса в ожидании оплаты.",
                messages.ERROR,
            )
        else:
            enqueue_invoice_creation(custom_game_request.id, payment_target="custom_game_request")
            self.message_user(request, "Создание инвойса поставлено в очередь.", messages.SUCCESS)

        return self._redirect_to_change(custom_game_request.pk)

    def resend_download_email_view(self, request, object_id: int):
        custom_game_request = self.get_object(request, object_id)
        if custom_game_request is None:
            self.message_user(request, "Заявка не найдена.", messages.ERROR)
            return self._redirect_to_changelist()
        if not self.has_change_permission(request, custom_game_request):
            raise PermissionDenied

        if not self._can_show_resend_download_email_button(custom_game_request):
            self.message_user(
                request,
                "Ссылку на скачивание можно отправить только переданной заявке с активным файлом.",
                messages.ERROR,
            )
        else:
            try:
                send_custom_game_download_link(custom_game_request=custom_game_request)
            except ValueError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                self.message_user(request, "Имейл со ссылкой на скачивание отправлен.", messages.SUCCESS)

        return self._redirect_to_change(custom_game_request.pk)

    def _redirect_to_change(self, object_id: int):
        return HttpResponseRedirect(reverse("admin:custom_games_customgamerequest_change", args=[object_id]))

    def _redirect_to_changelist(self):
        return HttpResponseRedirect(reverse("admin:custom_games_customgamerequest_changelist"))

    @admin.display(description=_("Файлы"))
    def files_summary(self, obj):
        if not obj.pk:
            return "Сохраните заявку перед добавлением файлов."

        files = list(obj.files.order_by("-is_active", "-uploaded_at", "-id"))
        add_url = f"{reverse('admin:custom_games_customgamefile_add')}?request={obj.pk}"

        if not files:
            return format_html(
                'Файлы не добавлены. <a href="{}">Добавить файл</a>',
                add_url,
            )

        rows = format_html_join(
            "",
            '<li>{}{}{} <a href="{}">Открыть</a></li>',
            (
                (
                    custom_game_file.original_filename or custom_game_file.file_key,
                    format_html(" ({})", custom_game_file.mime_type) if custom_game_file.mime_type else "",
                    format_html(" [active]") if custom_game_file.is_active else "",
                    reverse("admin:custom_games_customgamefile_change", args=[custom_game_file.pk]),
                )
                for custom_game_file in files
            ),
        )
        return format_html(
            '<ul style="margin:0 0 8px 0; padding-left:18px;">{}</ul><a href="{}">Добавить файл</a>',
            rows,
            add_url,
        )

    @admin.action(description=_("Перевести в работу"))
    def mark_in_progress(self, request, queryset):
        self._run_transition(request, queryset, "mark_in_progress", _("переведены в работу"))

    @admin.action(description=_("Отметить готовыми"))
    def mark_ready(self, request, queryset):
        self._run_transition(request, queryset, "mark_ready", _("отмечены готовыми"))

    @admin.action(description=_("Отменить"))
    def mark_cancelled(self, request, queryset):
        self._run_transition(request, queryset, "mark_cancelled", _("отменены"))

    def _run_transition(self, request, queryset, method_name: str, success_label: str) -> None:
        updated = 0
        errors = []
        for custom_game_request in queryset:
            try:
                getattr(custom_game_request, method_name)()
            except ValidationError as exc:
                errors.append(f"{custom_game_request.payment_account_no or custom_game_request.id}: {exc}")
            else:
                updated += 1

        if updated:
            self.message_user(request, f"{updated} заявок {success_label}.", messages.SUCCESS)
        for error in errors:
            self.message_user(request, error, messages.ERROR)


@admin.register(CustomGameFile)
class CustomGameFileAdmin(admin.ModelAdmin):
    form = CustomGameFileAdminForm
    list_display = ("id", "request", "original_filename", "size_bytes", "uploaded_at", "is_active")
    list_filter = ("is_active", "uploaded_at")
    search_fields = ("file_key", "original_filename", "request__payment_account_no", "request__contact_email")
    autocomplete_fields = ("request",)
    readonly_fields = (
        "file_key",
        "original_filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "uploaded_by",
        "uploaded_at",
        "created_at",
        "updated_at",
    )
    ordering = ("request", "-uploaded_at", "-id")
    fieldsets = (
        (
            None,
            {
                "fields": ("request", "upload", "is_active"),
            },
        ),
        (
            _("Файл в storage"),
            {
                "fields": (
                    "file_key",
                    "original_filename",
                    "mime_type",
                    "size_bytes",
                    "checksum_sha256",
                    "uploaded_by",
                    "uploaded_at",
                ),
            },
        ),
        (
            _("Даты"),
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        request_id = request.GET.get("request")
        if request_id:
            initial["request"] = request_id
        return initial

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        uploaded_file = form.cleaned_data.get("upload")
        previous_file_key = None

        if change:
            previous_file_key = type(obj).objects.only("file_key").get(pk=obj.pk).file_key

        if uploaded_file:
            uploaded_metadata = upload_custom_game_file(
                payment_account_no=obj.request.payment_account_no,
                uploaded_file=uploaded_file,
            )
            obj.file_key = uploaded_metadata["file_key"]
            obj.original_filename = uploaded_metadata["original_filename"]
            obj.mime_type = uploaded_metadata["mime_type"]
            obj.size_bytes = uploaded_metadata["size_bytes"]
            obj.checksum_sha256 = uploaded_metadata["checksum_sha256"]
            if not obj.uploaded_by_id and request.user.is_authenticated:
                obj.uploaded_by = request.user

        super().save_model(request, obj, form, change)

        if uploaded_file and previous_file_key and previous_file_key != obj.file_key:
            delete_product_file(file_key=previous_file_key)


@admin.register(CustomGameDownloadToken)
class CustomGameDownloadTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "token_prefix", "email", "expires_at", "downloads_count", "max_downloads")
    list_filter = ("expires_at", "sent_at", "created_at")
    search_fields = ("token_prefix", "email", "request__payment_account_no", "request__contact_email")
    autocomplete_fields = ("request",)
    readonly_fields = ("token_hash", "created_at", "updated_at")
