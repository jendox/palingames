from __future__ import annotations

import re

_GA4_CLIENT_ID_RE = re.compile(r"^\d{1,21}\.\d{1,21}$")
_GA4_SESSION_ID_RE = re.compile(r"^\d{1,21}$")
_GA_COOKIE_MIN_PARTS = 4


def normalize_ga4_client_id(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if not _GA4_CLIENT_ID_RE.fullmatch(cleaned):
        return ""
    return cleaned


def normalize_ga4_session_id(value: str | None) -> str:
    cleaned = str(value or "").strip()
    if not _GA4_SESSION_ID_RE.fullmatch(cleaned):
        return ""
    return cleaned


def parse_ga_client_id_from_ga_cookie(raw: str | None) -> str:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return ""
    parts = cleaned.split(".")
    if len(parts) < _GA_COOKIE_MIN_PARTS or parts[0] != "GA1":
        return ""
    return normalize_ga4_client_id(f"{parts[2]}.{parts[3]}")


def read_ga4_client_id_from_request(request) -> str:
    return parse_ga_client_id_from_ga_cookie(request.COOKIES.get("_ga"))
