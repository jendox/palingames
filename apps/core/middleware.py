from __future__ import annotations

import logging
import re
import time

from .logging import clear_logging_context, generate_request_id, log_event, set_logging_context
from .metrics import observe_http_request
from .sentry import configure_sentry_scope

logger = logging.getLogger("apps.request")

_MANAGED_LINK_PATH_RE = re.compile(r"^/go/[^/]+/")


def redact_request_path_for_logging(path: str) -> str:
    if _MANAGED_LINK_PATH_RE.match(path):
        return "/go/<redacted>/"
    return path


class RequestContextLoggingMiddleware:
    request_id_header = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started_at = time.monotonic()
        request_id = request.META.get(self.request_id_header) or generate_request_id()
        request.request_id = request_id
        clear_logging_context()
        logged_path = redact_request_path_for_logging(request.path)
        set_logging_context(
            request_id=request_id,
            http_method=request.method,
            path=logged_path,
        )
        configure_sentry_scope(
            request_id=request_id,
            http_method=request.method,
            path=logged_path,
        )
        log_event(logger, logging.INFO, "request.started")

        try:
            response = self.get_response(request)
        except Exception:
            duration_seconds = time.monotonic() - started_at
            observe_http_request(
                path=getattr(getattr(request, "resolver_match", None), "view_name", None) or "unresolved",
                method=request.method,
                status_code=500,
                duration_seconds=duration_seconds,
            )
            log_event(logger, logging.ERROR, "request.failed", status_code=500, exc_info=True)
            clear_logging_context()
            raise

        duration_seconds = time.monotonic() - started_at
        set_logging_context(status_code=response.status_code)
        configure_sentry_scope(status_code=response.status_code)
        observe_http_request(
            path=getattr(getattr(request, "resolver_match", None), "view_name", None) or "unresolved",
            method=request.method,
            status_code=response.status_code,
            duration_seconds=duration_seconds,
        )
        response[self.response_header] = request_id
        log_event(logger, logging.INFO, "request.finished", status_code=response.status_code)
        clear_logging_context()
        return response
