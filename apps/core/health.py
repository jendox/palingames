from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db import connections
from redis import Redis

from apps.products.services.s3 import get_s3_client


def check_database() -> None:
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_redis() -> None:
    client = Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    client.ping()


def check_s3() -> None:
    get_s3_client().head_bucket(Bucket=settings.S3_BUCKET_NAME)


def _run_check(check: Callable[[], None]) -> dict[str, str]:
    try:
        check()
    except Exception as exc:
        return {
            "status": "failed",
            "error": type(exc).__name__,
        }

    return {
        "status": "ok",
    }


def build_readiness_report() -> dict[str, dict[str, str]]:
    return {
        "database": _run_check(check_database),
        "redis": _run_check(check_redis),
        "s3": _run_check(check_s3),
    }
