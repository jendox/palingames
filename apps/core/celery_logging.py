from __future__ import annotations

import logging

from celery import Task
from celery.signals import task_postrun, task_prerun

from .logging import bind_task_logging_context, build_task_logging_headers, clear_logging_context, log_event

logger = logging.getLogger("apps.celery")


class ContextTask(Task):
    def apply_async(self, args=None, kwargs=None, task_id=None, producer=None, link=None, link_error=None, **options):
        headers = dict(options.get("headers") or {})
        headers.update(build_task_logging_headers())
        options["headers"] = headers
        return super().apply_async(
            args=args,
            kwargs=kwargs,
            task_id=task_id,
            producer=producer,
            link=link,
            link_error=link_error,
            **options,
        )


@task_prerun.connect
def bind_celery_task_context(task_id=None, task=None, **kwargs):
    headers = getattr(task.request, "headers", None) if task is not None else None
    bind_task_logging_context(task_id=task_id, task_name=getattr(task, "name", None), headers=headers)
    log_event(logger, logging.INFO, "task.started")


@task_postrun.connect
def clear_celery_task_context(task_id=None, task=None, state=None, **kwargs):
    log_event(
        logger,
        logging.INFO,
        "task.finished",
        task_id=task_id,
        task_name=getattr(task, "name", None),
        task_state=state,
    )
    clear_logging_context()
