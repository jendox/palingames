import logging

from celery import Task, shared_task

from apps.core.logging import log_event

from .services import (
    cleanup_old_notification_outboxes,
    process_notification_outbox,
    reap_stuck_notification_outbox_processing,
)
from .telegram_delivery import process_telegram_outbound_feedback, reap_stuck_telegram_outbox_deliveries

logger = logging.getLogger("apps.notifications.tasks")


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
    soft_time_limit=90,
    time_limit=120,
)
def send_notification_outbox_task(self: Task, outbox_id: int) -> None:
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.task.started",
        outbox_id=outbox_id,
        task_id=self.request.id,
    )
    process_notification_outbox(outbox_id=outbox_id)


@shared_task(bind=True)
def cleanup_notification_outbox_task(self: Task) -> dict[str, int]:
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.cleanup.started",
        task_id=self.request.id,
    )
    return cleanup_old_notification_outboxes()


@shared_task(bind=True)
def process_telegram_outbound_feedback_task(self: Task) -> dict[str, int]:
    log_event(
        logger,
        logging.INFO,
        "telegram.outbound.feedback.started",
        task_id=self.request.id,
    )
    return process_telegram_outbound_feedback()


@shared_task(bind=True)
def reap_stuck_telegram_outbox_deliveries_task(self: Task) -> int:
    log_event(
        logger,
        logging.INFO,
        "telegram.outbox.reaper.started",
        task_id=self.request.id,
    )
    return reap_stuck_telegram_outbox_deliveries()


@shared_task(bind=True)
def reap_stuck_notification_outbox_processing_task(self: Task) -> int:
    log_event(
        logger,
        logging.INFO,
        "notification.outbox.reaper.started",
        task_id=self.request.id,
    )
    return reap_stuck_notification_outbox_processing()
