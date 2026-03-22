from __future__ import annotations

import logging

from .logging import clear_logging_context, generate_request_id, log_event, set_logging_context

logger = logging.getLogger("apps.request")


class RequestContextLoggingMiddleware:
    request_id_header = "HTTP_X_REQUEST_ID"
    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.request_id_header) or generate_request_id()
        request.request_id = request_id
        clear_logging_context()
        set_logging_context(
            request_id=request_id,
            http_method=request.method,
            path=request.path,
        )
        log_event(logger, logging.INFO, "request.started")

        try:
            response = self.get_response(request)
        except Exception:
            log_event(logger, logging.ERROR, "request.failed", status_code=500, exc_info=True)
            clear_logging_context()
            raise

        set_logging_context(status_code=response.status_code)
        response[self.response_header] = request_id
        log_event(logger, logging.INFO, "request.finished", status_code=response.status_code)
        clear_logging_context()
        return response
