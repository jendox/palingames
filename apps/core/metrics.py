from __future__ import annotations

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
except ImportError:  # pragma: no cover - optional until dependencies are synced
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoopMetric:
        def labels(self, **kwargs):
            return self

        def inc(self, amount: float = 1.0) -> None:
            return None

        def observe(self, value: float) -> None:
            return None

    def Counter(*args, **kwargs):  # noqa: N802
        return _NoopMetric()

    def Histogram(*args, **kwargs):  # noqa: N802
        return _NoopMetric()

    def generate_latest() -> bytes:
        return b"# prometheus_client is not installed\n"

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["path", "method", "status_code"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["path", "method"],
)
ORDERS_CREATED_TOTAL = Counter(
    "orders_created_total",
    "Total created orders.",
    ["checkout_type", "source"],
)
ORDERS_PAID_TOTAL = Counter(
    "orders_paid_total",
    "Total paid orders.",
    ["checkout_type", "source"],
)
ORDERS_PAID_DUPLICATE_TOTAL = Counter(
    "orders_paid_duplicate_total",
    "Total duplicate paid order transitions skipped after an order was already paid.",
    ["checkout_type", "source"],
)
INVOICES_CREATED_TOTAL = Counter(
    "invoices_created_total",
    "Total created invoices.",
    ["provider"],
)
PAYMENT_WEBHOOKS_RECEIVED_TOTAL = Counter(
    "payment_webhooks_received_total",
    "Total payment webhooks received.",
    ["provider", "cmd_type"],
)
PAYMENT_WEBHOOKS_FAILED_TOTAL = Counter(
    "payment_webhooks_failed_total",
    "Total failed payment webhooks.",
    ["provider", "reason"],
)
PAYMENT_WEBHOOKS_REJECTED_TOTAL = Counter(
    "payment_webhooks_rejected_total",
    "Total rejected payment webhooks.",
    ["provider", "reason"],
)
PAYMENT_DUPLICATE_EVENTS_TOTAL = Counter(
    "payment_duplicate_events_total",
    "Total duplicate payment events received.",
    ["provider", "cmd_type", "source"],
)
INVOICE_STATUS_SYNC_RUNS_TOTAL = Counter(
    "invoice_status_sync_runs_total",
    "Total invoice status sync runs.",
)
INVOICE_STATUS_SYNC_SELECTED_TOTAL = Counter(
    "invoice_status_sync_selected_total",
    "Total invoices selected for status sync.",
)
INVOICE_STATUS_SYNC_PROCESSED_TOTAL = Counter(
    "invoice_status_sync_processed_total",
    "Total processed invoice sync outcomes.",
    ["result"],
)
INVOICE_STATUS_SYNC_FAILED_TOTAL = Counter(
    "invoice_status_sync_failed_total",
    "Total failed invoice sync attempts.",
)
GUEST_EMAIL_OUTBOX_CREATED_TOTAL = Counter(
    "guest_email_outbox_created_total",
    "Total guest email outbox entries created.",
)
GUEST_EMAIL_SENT_TOTAL = Counter(
    "guest_email_sent_total",
    "Total guest emails sent successfully.",
)
GUEST_EMAIL_FAILED_TOTAL = Counter(
    "guest_email_failed_total",
    "Total failed guest email sends.",
)
PRODUCT_DOWNLOAD_REDIRECT_TOTAL = Counter(
    "product_download_redirect_total",
    "Total successful product download redirects.",
    ["access_type"],
)
PRODUCT_DOWNLOAD_FAILED_TOTAL = Counter(
    "product_download_failed_total",
    "Total failed product download attempts.",
    ["access_type", "reason"],
)
HEALTH_READINESS_CHECKS_TOTAL = Counter(
    "health_readiness_checks_total",
    "Total readiness checks by component and status.",
    ["component", "status"],
)
CELERY_TASK_STARTED_TOTAL = Counter(
    "celery_task_started_total",
    "Total Celery tasks started.",
    ["task_name"],
)
CELERY_TASK_FINISHED_TOTAL = Counter(
    "celery_task_finished_total",
    "Total Celery tasks finished.",
    ["task_name", "task_state"],
)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def observe_http_request(*, path: str, method: str, status_code: int, duration_seconds: float) -> None:
    HTTP_REQUESTS_TOTAL.labels(path=path, method=method, status_code=str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(path=path, method=method).observe(duration_seconds)


def inc_order_created(*, checkout_type: str, source: str) -> None:
    ORDERS_CREATED_TOTAL.labels(checkout_type=checkout_type, source=source).inc()


def inc_order_paid(*, checkout_type: str, source: str) -> None:
    ORDERS_PAID_TOTAL.labels(checkout_type=checkout_type, source=source).inc()


def inc_order_paid_duplicate(*, checkout_type: str, source: str) -> None:
    ORDERS_PAID_DUPLICATE_TOTAL.labels(checkout_type=checkout_type, source=source).inc()


def inc_invoice_created(*, provider: str) -> None:
    INVOICES_CREATED_TOTAL.labels(provider=provider).inc()


def inc_payment_webhook_received(*, provider: str, cmd_type: int | str) -> None:
    PAYMENT_WEBHOOKS_RECEIVED_TOTAL.labels(provider=provider, cmd_type=str(cmd_type)).inc()


def inc_payment_webhook_failed(*, provider: str, reason: str) -> None:
    PAYMENT_WEBHOOKS_FAILED_TOTAL.labels(provider=provider, reason=reason).inc()


def inc_payment_webhook_rejected(*, provider: str, reason: str) -> None:
    PAYMENT_WEBHOOKS_REJECTED_TOTAL.labels(provider=provider, reason=reason).inc()


def inc_payment_duplicate_event(*, provider: str, cmd_type: int | str, source: str) -> None:
    PAYMENT_DUPLICATE_EVENTS_TOTAL.labels(provider=provider, cmd_type=str(cmd_type), source=source).inc()


def record_invoice_status_sync_summary(summary: dict[str, int]) -> None:
    INVOICE_STATUS_SYNC_RUNS_TOTAL.inc()
    INVOICE_STATUS_SYNC_SELECTED_TOTAL.inc(summary.get("selected", 0))
    INVOICE_STATUS_SYNC_FAILED_TOTAL.inc(summary.get("failed", 0))
    for result in ("paid", "expired", "canceled", "pending", "refunded", "unknown", "skipped"):
        INVOICE_STATUS_SYNC_PROCESSED_TOTAL.labels(result=result).inc(summary.get(result, 0))


def inc_guest_email_outbox_created() -> None:
    GUEST_EMAIL_OUTBOX_CREATED_TOTAL.inc()


def inc_guest_email_sent() -> None:
    GUEST_EMAIL_SENT_TOTAL.inc()


def inc_guest_email_failed() -> None:
    GUEST_EMAIL_FAILED_TOTAL.inc()


def inc_product_download_redirect(*, access_type: str) -> None:
    PRODUCT_DOWNLOAD_REDIRECT_TOTAL.labels(access_type=access_type).inc()


def inc_product_download_failed(*, access_type: str, reason: str) -> None:
    PRODUCT_DOWNLOAD_FAILED_TOTAL.labels(access_type=access_type, reason=reason).inc()


def inc_health_readiness_check(*, component: str, status: str) -> None:
    HEALTH_READINESS_CHECKS_TOTAL.labels(component=component, status=status).inc()


def inc_celery_task_started(*, task_name: str | None) -> None:
    CELERY_TASK_STARTED_TOTAL.labels(task_name=task_name or "unknown").inc()


def inc_celery_task_finished(*, task_name: str | None, task_state: str | None) -> None:
    CELERY_TASK_FINISHED_TOTAL.labels(
        task_name=task_name or "unknown",
        task_state=task_state or "unknown",
    ).inc()
