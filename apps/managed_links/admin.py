from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin_site import admin_site
from apps.core.logging import log_event
from apps.core.metrics import inc_managed_link_qr_generated
from apps.core.seo import build_absolute_url
from apps.managed_links.forms import ManagedLinkAdminForm
from apps.managed_links.models import ManagedLink
from apps.managed_links.services.qr import (
    QrGenerationError,
    generate_qr_png,
    generate_qr_svg,
    qr_png_content_disposition,
    qr_svg_content_disposition,
)

logger = logging.getLogger("apps.managed_links.qr")


@admin.register(ManagedLink, site=admin_site)
class ManagedLinkAdmin(admin.ModelAdmin):
    form = ManagedLinkAdminForm
    change_form_template = "admin/managed_links/managedlink/change_form.html"
    list_display = (
        "title",
        "display_active_source",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "token_prefix", "token", "external_url", "s3_file_key", "original_filename")
    readonly_fields = (
        "token",
        "permanent_url_display",
        "qr_preview",
        "qr_download_links",
        "site_base_url_display",
        "display_active_source",
        "display_has_fallback",
        "s3_file_key",
        "original_filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at", "-id")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "is_active",
                    "external_url",
                    "site_base_url_display",
                    "token",
                    "permanent_url_display",
                ),
            },
        ),
        (
            _("QR-код"),
            {
                "fields": (
                    "qr_logo",
                    "qr_preview",
                    "qr_download_links",
                ),
            },
        ),
        (
            _("Файл в storage"),
            {
                "fields": (
                    "display_active_source",
                    "display_has_fallback",
                    "s3_file_key",
                    "original_filename",
                    "mime_type",
                    "size_bytes",
                    "checksum_sha256",
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/qr.png",
                self.admin_site.admin_view(self.qr_png_view),
                name="managed_links_managedlink_qr_png",
            ),
            path(
                "<path:object_id>/qr.svg",
                self.admin_site.admin_view(self.qr_svg_view),
                name="managed_links_managedlink_qr_svg",
            ),
        ]
        return custom_urls + urls

    def save_model(self, request, obj, form, change):
        forced_inactive = (
            obj.is_active
            and not (obj.s3_file_key or "").strip()
            and not (obj.external_url or "").strip()
        )
        if forced_inactive:
            obj.is_active = False
        super().save_model(request, obj, form, change)
        if forced_inactive:
            self.message_user(
                request,
                _(
                    "Ссылка сохранена как неактивная: загрузите файл в S3 или укажите внешнюю ссылку, "
                    "затем включите «Активна» (после загрузки в S3 активация произойдёт автоматически).",
                ),
                level=messages.WARNING,
            )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = {
            **(extra_context or {}),
            **self._direct_s3_upload_context(object_id),
        }
        return super().changeform_view(request, object_id, form_url, extra_context)

    def _direct_s3_upload_context(self, object_id: str | None) -> dict:
        context = {
            "managed_link_direct_s3_upload_enabled": settings.MANAGED_LINK_DIRECT_S3_UPLOAD_ENABLED,
        }
        if not settings.MANAGED_LINK_DIRECT_S3_UPLOAD_ENABLED or not object_id:
            return context

        context["managed_link_direct_s3_upload_config"] = {
            "presignUrl": reverse("admin-managed-link-presign"),
            "finalizeUrl": reverse("admin-managed-link-finalize"),
            "managedLinkId": int(object_id),
            "maxBytes": settings.MANAGED_LINK_UPLOAD_MAX_BYTES,
        }
        return context

    @admin.display(description=_("Постоянный URL"))
    def permanent_url_display(self, obj: ManagedLink) -> str:
        if not obj.pk:
            return "Сохраните ссылку, чтобы получить постоянный URL."
        url = obj.permanent_url
        return format_html(
            '<input type="text" readonly value="{}" style="width:100%;max-width:640px;" '
            'onclick="this.select();document.execCommand(\'copy\');" />',
            url,
        )

    @admin.display(description=_("Домен QR"))
    def site_base_url_display(self, _obj: ManagedLink) -> str:
        return build_absolute_url("/")

    @admin.display(description=_("Основной источник"))
    def display_active_source(self, obj: ManagedLink) -> str:
        labels = {
            ManagedLink.ActiveSource.S3: "S3",
            ManagedLink.ActiveSource.EXTERNAL: "Внешняя ссылка",
            ManagedLink.ActiveSource.UNAVAILABLE: "Недоступно",
        }
        return labels.get(obj.active_source, obj.active_source)

    @admin.display(description=_("Fallback"))
    def display_has_fallback(self, obj: ManagedLink) -> str:
        if obj.s3_file_key and obj.external_url:
            return "external_url ✓"
        if obj.external_url and not obj.s3_file_key:
            return "—"
        return "нет"

    @admin.display(description=_("Preview QR"))
    def qr_preview(self, obj: ManagedLink) -> str:
        if not obj.pk:
            return "Сохраните ссылку, чтобы увидеть QR-код."
        preview_url = reverse("admin:managed_links_managedlink_qr_png", args=[obj.pk])
        return format_html(
            '<img src="{}?preview=1" alt="QR preview" style="max-width:220px;border:1px solid #ddd;" />',
            preview_url,
        )

    @admin.display(description=_("Скачать QR"))
    def qr_download_links(self, obj: ManagedLink) -> str:
        if not obj.pk:
            return "—"
        png_url = reverse("admin:managed_links_managedlink_qr_png", args=[obj.pk])
        svg_url = reverse("admin:managed_links_managedlink_qr_svg", args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">PNG</a>&nbsp;<a class="button" href="{}">SVG (без логотипа)</a>',
            png_url,
            svg_url,
        )

    def qr_png_view(self, request, object_id: str, *args, **kwargs):
        managed_link = self.get_object(request, object_id)
        if managed_link is None:
            return HttpResponse(status=404)
        try:
            png_bytes = generate_qr_png(managed_link=managed_link, with_logo=True)
        except QrGenerationError as exc:
            log_event(
                logger,
                logging.ERROR,
                "managed_link.qr.failed",
                exc_info=exc,
                managed_link_id=managed_link.id,
                format="png",
                error_type=type(exc).__name__,
            )
            return HttpResponse(status=500)
        inc_managed_link_qr_generated(qr_format="png", with_logo="true")
        response = HttpResponse(png_bytes, content_type="image/png")
        response["Cache-Control"] = "no-store"
        if not request.GET.get("preview"):
            response["Content-Disposition"] = qr_png_content_disposition(managed_link=managed_link)
        return response

    def qr_svg_view(self, request, object_id: str, *args, **kwargs):
        managed_link = self.get_object(request, object_id)
        if managed_link is None:
            return HttpResponse(status=404)
        try:
            svg_bytes = generate_qr_svg(managed_link=managed_link)
        except QrGenerationError as exc:
            log_event(
                logger,
                logging.ERROR,
                "managed_link.qr.failed",
                exc_info=exc,
                managed_link_id=managed_link.id,
                format="svg",
                error_type=type(exc).__name__,
            )
            return HttpResponse(status=500)
        inc_managed_link_qr_generated(qr_format="svg", with_logo="false")
        response = HttpResponse(svg_bytes, content_type="image/svg+xml")
        response["Cache-Control"] = "no-store"
        response["Content-Disposition"] = qr_svg_content_disposition(managed_link=managed_link)
        return response
