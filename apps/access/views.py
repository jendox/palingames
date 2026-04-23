from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views import View

from apps.core.logging import log_event
from apps.core.metrics import inc_product_download_failed, inc_product_download_redirect
from apps.products.alerts import record_download_delivery_failure_incident
from apps.products.services.s3 import ProductFileDownloadUrlError, generate_presigned_download_url

from .services import mark_guest_access_used, release_guest_access_use, resolve_guest_access

logger = logging.getLogger("apps.access")


class GuestProductDownloadView(View):
    http_method_names = ["get"]
    template_name = "access/guest_download_invalid.html"

    def get(self, request, token: str, *args, **kwargs):
        guest_access = resolve_guest_access(token)
        if guest_access is None:
            inc_product_download_failed(access_type="guest", reason="invalid_or_expired")
            return self._render_invalid(
                request,
                reason="invalid_or_expired",
                status=410,
            )

        product_file = guest_access.product.files.filter(is_active=True).first()
        if product_file is None:
            log_event(
                logger,
                logging.WARNING,
                "guest_access.download.unavailable",
                guest_access_id=guest_access.id,
                order_id=guest_access.order_id,
                product_id=guest_access.product_id,
                reason="active_file_not_found",
            )
            inc_product_download_failed(access_type="guest", reason="file_not_found")
            return self._render_invalid(
                request,
                reason="file_not_found",
                status=404,
            )

        if not mark_guest_access_used(guest_access):
            log_event(
                logger,
                logging.WARNING,
                "guest_access.download.rejected",
                guest_access_id=guest_access.id,
                order_id=guest_access.order_id,
                product_id=guest_access.product_id,
                reason="download_limit_exhausted",
            )
            inc_product_download_failed(access_type="guest", reason="download_limit_exhausted")
            return self._render_invalid(
                request,
                reason="invalid_or_expired",
                status=410,
            )

        try:
            download_url = generate_presigned_download_url(
                file_key=product_file.file_key,
                original_filename=product_file.original_filename or f"{guest_access.product.slug}.zip",
            )
        except ProductFileDownloadUrlError as exc:
            release_guest_access_use(guest_access)
            log_event(
                logger,
                logging.ERROR,
                "guest_access.download.failed",
                exc_info=exc,
                guest_access_id=guest_access.id,
                order_id=guest_access.order_id,
                product_id=guest_access.product_id,
                file_key=product_file.file_key,
                error_type=type(exc).__name__,
            )
            inc_product_download_failed(access_type="guest", reason="download_unavailable")
            record_download_delivery_failure_incident(
                delivery_type="guest_product",
                reason="download_unavailable",
                threshold=settings.DOWNLOAD_DELIVERY_INCIDENT_THRESHOLD,
                window_seconds=settings.DOWNLOAD_DELIVERY_INCIDENT_WINDOW_SECONDS,
            )
            return self._render_invalid(
                request,
                reason="download_unavailable",
                status=503,
            )
        log_event(
            logger,
            logging.INFO,
            "guest_access.download.redirected",
            guest_access_id=guest_access.id,
            order_id=guest_access.order_id,
            product_id=guest_access.product_id,
            downloads_count=guest_access.downloads_count,
            max_downloads=guest_access.max_downloads,
        )
        inc_product_download_redirect(access_type="guest")
        return HttpResponseRedirect(download_url)

    def _render_invalid(self, request, *, reason: str, status: int):
        return render(
            request,
            self.template_name,
            {
                "guest_download_reason": reason,
            },
            status=status,
        )
