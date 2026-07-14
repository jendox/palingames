from __future__ import annotations

SESSION_KEY_PENDING_ANALYTICS_EVENTS = "pending_analytics_events"
SESSION_KEY_ANALYTICS_SUPPRESS_LOGIN = "analytics_suppress_login"

ALLOWED_CLIENT_ANALYTICS_EVENTS = frozenset({"sign_up", "login"})


def extract_file_extension(filename: str) -> str:
    normalized = (filename or "").strip().lower()
    if "." not in normalized:
        return ""
    return normalized.rsplit(".", 1)[-1]


def map_auth_method(latest_auth_method: dict | None) -> str:
    if not latest_auth_method:
        return "email"

    if latest_auth_method.get("method") == "socialaccount":
        provider = latest_auth_method.get("provider") or ""
        if provider in {"google", "yandex"}:
            return provider
        return "social"

    return "email"


def map_social_provider(provider: str | None) -> str:
    if provider in {"google", "yandex"}:
        return provider
    return "social"


def _normalize_client_event_payload(event: str, payload: dict | None) -> dict:
    normalized = dict(payload or {})
    if event in {"sign_up", "login"}:
        method = normalized.get("method")
        if isinstance(method, str) and method:
            return {"method": method}
        return {"method": "email"}
    return normalized


def _mark_session_modified(session) -> None:
    if hasattr(session, "modified"):
        session.modified = True


def queue_client_analytics_event(request, *, event: str, payload: dict | None = None) -> None:
    if request is None or not hasattr(request, "session"):
        return
    if event not in ALLOWED_CLIENT_ANALYTICS_EVENTS:
        return

    pending = request.session.get(SESSION_KEY_PENDING_ANALYTICS_EVENTS)
    if not isinstance(pending, list):
        pending = []

    pending.append(
        {
            "event": event,
            **_normalize_client_event_payload(event, payload),
        },
    )
    request.session[SESSION_KEY_PENDING_ANALYTICS_EVENTS] = pending
    _mark_session_modified(request.session)


def suppress_login_analytics_event(request) -> None:
    if request is None or not hasattr(request, "session"):
        return
    request.session[SESSION_KEY_ANALYTICS_SUPPRESS_LOGIN] = True
    _mark_session_modified(request.session)


def consume_login_analytics_suppression(request) -> bool:
    if request is None or not hasattr(request, "session"):
        return False
    if not request.session.pop(SESSION_KEY_ANALYTICS_SUPPRESS_LOGIN, False):
        return False
    _mark_session_modified(request.session)
    return True


def consume_pending_analytics_events(request) -> list[dict]:
    if request is None or not hasattr(request, "session"):
        return []

    pending = request.session.pop(SESSION_KEY_PENDING_ANALYTICS_EVENTS, None)
    if not isinstance(pending, list) or not pending:
        return []

    _mark_session_modified(request.session)
    return [event for event in pending if isinstance(event, dict) and event.get("event")]


def build_account_download_analytics_payload(
    *,
    item_id: str,
    item_name: str,
    item_category: str = "",
    item_variant: str = "",
    file_extension: str = "",
) -> dict:
    return {
        "event": "file_download_account",
        "file_name": item_name,
        "file_extension": file_extension,
        "item_id": item_id,
        "item_name": item_name,
        "item_category": item_category,
        "item_variant": item_variant,
        "download_type": "account",
    }


def build_product_file_download_analytics_payload(
    *,
    product,
    product_file,
    primary_category=None,
    primary_kind=None,
) -> dict:
    category = primary_category or product.categories.first()
    kind = primary_kind or product.subtypes.first() or category
    original_filename = product_file.original_filename if product_file else ""
    return {
        "event": "file_download_account",
        "file_name": product.title,
        "file_extension": extract_file_extension(original_filename or f"{product.slug}.zip"),
        "item_id": str(product.id),
        "item_name": product.title,
        "item_category": category.title if category else "",
        "item_variant": kind.title if kind else "",
        "download_type": "account",
    }
