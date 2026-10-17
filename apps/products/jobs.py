from __future__ import annotations

import logging
from collections.abc import Iterable

from django.db import transaction

from apps.core.logging import log_event
from apps.products.models import Product
from apps.products.tasks import generate_product_image_thumbnails_task, run_product_smoke_check_task

logger = logging.getLogger("apps.products.jobs")


def should_enqueue_product_smoke_check(*, is_published: bool) -> bool:
    return is_published


def enqueue_product_smoke_check(product_id: int) -> None:
    log_event(
        logger,
        logging.INFO,
        "product_smoke_check.enqueued",
        product_id=product_id,
    )
    transaction.on_commit(lambda: run_product_smoke_check_task.delay(product_id))


def enqueue_product_smoke_checks_for_ids(product_ids: Iterable[int]) -> None:
    queued_ids = list(product_ids)
    if not queued_ids:
        return

    log_event(
        logger,
        logging.INFO,
        "product_smoke_check.enqueued_bulk",
        product_ids=queued_ids,
        count=len(queued_ids),
    )

    def _enqueue_all() -> None:
        for product_id in queued_ids:
            run_product_smoke_check_task.delay(product_id)

    transaction.on_commit(_enqueue_all)


def maybe_enqueue_product_smoke_check(product_id: int) -> None:
    if not Product.objects.filter(pk=product_id, is_published=True).exists():
        return
    enqueue_product_smoke_check(product_id)


def enqueue_product_image_thumbnails(product_image_id: int, *, force: bool = False) -> None:
    from kombu.exceptions import OperationalError as KombuOperationalError

    log_event(
        logger,
        logging.INFO,
        "product_image.thumbnails.enqueued",
        product_image_id=product_image_id,
        force=force,
    )

    def _dispatch() -> None:
        try:
            generate_product_image_thumbnails_task.delay(product_image_id, force=force)
        except KombuOperationalError as exc:
            log_event(
                logger,
                logging.WARNING,
                "product_image.thumbnails.enqueue_failed",
                product_image_id=product_image_id,
                force=force,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    transaction.on_commit(_dispatch)
