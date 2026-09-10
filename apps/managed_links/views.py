from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.views import View

from apps.core.logging import log_event
from apps.core.metrics import inc_managed_link_redirect, inc_managed_link_redirect_failed
from apps.core.rate_limits import RateLimitScope, check_rate_limit, get_client_ip
from apps.managed_links.models import ManagedLink
from apps.managed_links.services.redirect import resolve_managed_link_redirect
from apps.managed_links.services.storage import ManagedLinkDownloadUrlError
from apps.managed_links.tokens import managed_link_token_prefix
from apps.products.alerts import record_storage_unavailable_incident

logger = logging.getLogger("apps.managed_links")


class ManagedLinkRedirectView(View):
    http_method_names = ["get", "head"]

    def _apply_redirect_headers(self, response: HttpResponse) -> HttpResponse:
        response["Cache-Control"] = "no-store"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def _not_found_response(self) -> HttpResponse:
        return self._apply_redirect_headers(HttpResponse(status=404))

    def _is_rate_limit_allowed(self, request) -> bool:
        client_ip = get_client_ip(request)
        if not client_ip:
            return True
        result = check_rate_limit(
            scope=RateLimitScope.MANAGED_LINK_REDIRECT,
            identifier=f"ip:{client_ip}",
            limit=settings.MANAGED_LINK_REDIRECT_IP_RATE_LIMIT,
            window_seconds=settings.MANAGED_LINK_REDIRECT_IP_RATE_LIMIT_WINDOW_SECONDS,
        )
        return result.allowed

    def get(self, request, token: str, *args, **kwargs):
        if not self._is_rate_limit_allowed(request):
            inc_managed_link_redirect_failed(reason="rate_limited")
            return self._not_found_response()

        managed_link = ManagedLink.objects.filter(token=token, is_active=True).first()
        if managed_link is None:
            log_event(
                logger,
                logging.INFO,
                "managed_link.redirect.not_found",
                token_prefix=managed_link_token_prefix(token),
            )
            inc_managed_link_redirect_failed(reason="not_found")
            return self._not_found_response()

        try:
            redirect_result = resolve_managed_link_redirect(managed_link=managed_link)
        except ManagedLinkDownloadUrlError as exc:
            log_event(
                logger,
                logging.ERROR,
                "managed_link.redirect.s3_failed",
                exc_info=exc,
                managed_link_id=managed_link.id,
                token_prefix=managed_link.token_prefix,
                error_type=type(exc).__name__,
            )
            record_storage_unavailable_incident(
                operation="managed_link_generate_presigned_download_url",
                threshold=settings.STORAGE_INCIDENT_THRESHOLD,
                window_seconds=settings.STORAGE_INCIDENT_WINDOW_SECONDS,
            )
            inc_managed_link_redirect_failed(reason="s3")
            return self._apply_redirect_headers(HttpResponse(status=503))
        except ValidationError:
            log_event(
                logger,
                logging.ERROR,
                "managed_link.redirect.no_destination",
                managed_link_id=managed_link.id,
                token_prefix=managed_link.token_prefix,
            )
            inc_managed_link_redirect_failed(reason="no_destination")
            return self._not_found_response()

        log_event(
            logger,
            logging.INFO,
            "managed_link.redirect.success",
            managed_link_id=managed_link.id,
            token_prefix=managed_link.token_prefix,
            source=redirect_result.source,
        )
        inc_managed_link_redirect(source=redirect_result.source)
        return self._apply_redirect_headers(HttpResponseRedirect(redirect_result.url))

    def head(self, request, token: str, *args, **kwargs):
        response = self.get(request, token, *args, **kwargs)
        if isinstance(response, HttpResponseRedirect):
            head_response = HttpResponse(status=response.status_code)
            head_response["Location"] = response["Location"]
            for header in ("Cache-Control", "X-Robots-Tag", "Referrer-Policy"):
                if header in response:
                    head_response[header] = response[header]
            return head_response
        if isinstance(response, HttpResponse):
            head_response = HttpResponse(status=response.status_code)
            for header in ("Cache-Control", "X-Robots-Tag", "Referrer-Policy"):
                if header in response:
                    head_response[header] = response[header]
            return head_response
        return response
