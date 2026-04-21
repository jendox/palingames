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
ORDER_CREATION_DURATION_SECONDS = Histogram(
    "order_creation_duration_seconds",
    "Order creation duration in seconds.",
    ["checkout_type", "result"],
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
PAYMENT_WEBHOOK_PROCESSING_DURATION_SECONDS = Histogram(
    "payment_webhook_processing_duration_seconds",
    "Payment webhook/status update processing duration in seconds.",
    ["provider", "source", "result"],
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
CUSTOM_GAME_REQUEST_CREATION_DURATION_SECONDS = Histogram(
    "custom_game_request_creation_duration_seconds",
    "Custom game request creation duration in seconds.",
    ["user_type", "result"],
)
REVIEWS_SUBMITTED_TOTAL = Counter(
    "reviews_submitted_total",
    "Total newly submitted reviews.",
)
REVIEWS_RESUBMITTED_TOTAL = Counter(
    "reviews_resubmitted_total",
    "Total resubmitted reviews after rejection.",
)
REVIEWS_PUBLISHED_TOTAL = Counter(
    "reviews_published_total",
    "Total reviews published by moderation.",
)
REVIEWS_REJECTED_TOTAL = Counter(
    "reviews_rejected_total",
    "Total reviews rejected by moderation.",
)
REVIEW_REWARDS_ISSUED_TOTAL = Counter(
    "review_rewards_issued_total",
    "Total reward promo codes issued for published reviews.",
)
ORDER_REWARDS_ISSUED_TOTAL = Counter(
    "order_rewards_issued_total",
    "Total reward promo codes issued for paid orders.",
)
ORDER_REWARDS_SKIPPED_TOTAL = Counter(
    "order_rewards_skipped_total",
    "Total order reward skips by reason.",
    ["reason"],
)
GUEST_ORDERS_MERGED_TOTAL = Counter(
    "guest_orders_merged_total",
    "Total guest orders merged into user accounts.",
)
CATALOG_PAGE_VIEWS_TOTAL = Counter(
    "catalog_page_views_total",
    "Total catalog page views.",
    ["user_type"],
)
PRODUCT_PAGE_VIEWS_TOTAL = Counter(
    "product_page_views_total",
    "Total product detail page views.",
    ["user_type"],
)
PRODUCT_ADDED_TO_CART_TOTAL = Counter(
    "product_added_to_cart_total",
    "Total products added to cart.",
    ["user_type"],
)
CHECKOUT_STARTED_TOTAL = Counter(
    "checkout_started_total",
    "Total checkout starts.",
    ["user_type"],
)
CHECKOUT_COMPLETED_TOTAL = Counter(
    "checkout_completed_total",
    "Total completed checkout submissions that created an order.",
    ["checkout_type", "source"],
)
AUTH_LOGIN_SUCCESS_TOTAL = Counter(
    "auth_login_success_total",
    "Total successful login events.",
    ["method"],
)
AUTH_LOGIN_FAILED_TOTAL = Counter(
    "auth_login_failed_total",
    "Total failed login events.",
    ["login_field"],
)
AUTH_SIGNUP_SUCCESS_TOTAL = Counter(
    "auth_signup_success_total",
    "Total successful signup events.",
    ["method"],
)
AUTH_PASSWORD_RESET_REQUESTED_TOTAL = Counter(
    "auth_password_reset_requested_total",
    "Total password reset emails requested and sent.",
)
AUTH_PASSWORD_RESET_COMPLETED_TOTAL = Counter(
    "auth_password_reset_completed_total",
    "Total completed password reset events.",
)
AUTH_RATE_LIMIT_TRIGGERED_TOTAL = Counter(
    "auth_rate_limit_triggered_total",
    "Total auth rate limit blocks.",
    ["scope", "identifier_type"],
)
PROMO_APPLY_SUCCESS_TOTAL = Counter(
    "promo_apply_success_total",
    "Total successful promo code apply attempts.",
)
PROMO_APPLY_FAILED_TOTAL = Counter(
    "promo_apply_failed_total",
    "Total failed promo code apply attempts by reason.",
    ["reason"],
)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def observe_http_request(*, path: str, method: str, status_code: int, duration_seconds: float) -> None:
    HTTP_REQUESTS_TOTAL.labels(path=path, method=method, status_code=str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(path=path, method=method).observe(duration_seconds)


def inc_order_created(*, checkout_type: str, source: str) -> None:
    ORDERS_CREATED_TOTAL.labels(checkout_type=checkout_type, source=source).inc()


def observe_order_creation_duration(*, checkout_type: str, result: str, duration_seconds: float) -> None:
    ORDER_CREATION_DURATION_SECONDS.labels(checkout_type=checkout_type, result=result).observe(duration_seconds)


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


def observe_payment_webhook_processing_duration(
    *,
    provider: str,
    source: str,
    result: str,
    duration_seconds: float,
) -> None:
    PAYMENT_WEBHOOK_PROCESSING_DURATION_SECONDS.labels(
        provider=provider,
        source=source,
        result=result,
    ).observe(duration_seconds)


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


def inc_review_submitted() -> None:
    REVIEWS_SUBMITTED_TOTAL.inc()


def inc_review_resubmitted() -> None:
    REVIEWS_RESUBMITTED_TOTAL.inc()


def inc_review_published() -> None:
    REVIEWS_PUBLISHED_TOTAL.inc()


def inc_review_rejected() -> None:
    REVIEWS_REJECTED_TOTAL.inc()


def inc_review_reward_issued() -> None:
    REVIEW_REWARDS_ISSUED_TOTAL.inc()


def inc_order_reward_issued() -> None:
    ORDER_REWARDS_ISSUED_TOTAL.inc()


def inc_order_reward_skipped(*, reason: str) -> None:
    ORDER_REWARDS_SKIPPED_TOTAL.labels(reason=reason).inc()


def inc_guest_orders_merged(*, count: int = 1) -> None:
    GUEST_ORDERS_MERGED_TOTAL.inc(count)


def observe_custom_game_request_creation_duration(
    *,
    user_type: str,
    result: str,
    duration_seconds: float,
) -> None:
    CUSTOM_GAME_REQUEST_CREATION_DURATION_SECONDS.labels(
        user_type=user_type,
        result=result,
    ).observe(duration_seconds)


def inc_catalog_page_view(*, user_type: str) -> None:
    CATALOG_PAGE_VIEWS_TOTAL.labels(user_type=user_type).inc()


def inc_product_page_view(*, user_type: str) -> None:
    PRODUCT_PAGE_VIEWS_TOTAL.labels(user_type=user_type).inc()


def inc_product_added_to_cart(*, user_type: str) -> None:
    PRODUCT_ADDED_TO_CART_TOTAL.labels(user_type=user_type).inc()


def inc_checkout_started(*, user_type: str) -> None:
    CHECKOUT_STARTED_TOTAL.labels(user_type=user_type).inc()


def inc_checkout_completed(*, checkout_type: str, source: str) -> None:
    CHECKOUT_COMPLETED_TOTAL.labels(checkout_type=checkout_type, source=source).inc()


def inc_auth_login_success(*, method: str) -> None:
    AUTH_LOGIN_SUCCESS_TOTAL.labels(method=method).inc()


def inc_auth_login_failed(*, login_field: str) -> None:
    AUTH_LOGIN_FAILED_TOTAL.labels(login_field=login_field).inc()


def inc_auth_signup_success(*, method: str) -> None:
    AUTH_SIGNUP_SUCCESS_TOTAL.labels(method=method).inc()


def inc_auth_password_reset_requested() -> None:
    AUTH_PASSWORD_RESET_REQUESTED_TOTAL.inc()


def inc_auth_password_reset_completed() -> None:
    AUTH_PASSWORD_RESET_COMPLETED_TOTAL.inc()


def inc_auth_rate_limit_triggered(*, scope: str, identifier_type: str) -> None:
    AUTH_RATE_LIMIT_TRIGGERED_TOTAL.labels(
        scope=scope,
        identifier_type=identifier_type or "unknown",
    ).inc()


def inc_promo_apply_succeeded() -> None:
    PROMO_APPLY_SUCCESS_TOTAL.inc()


def inc_promo_apply_failed(*, reason: str) -> None:
    PROMO_APPLY_FAILED_TOTAL.labels(reason=reason).inc()
