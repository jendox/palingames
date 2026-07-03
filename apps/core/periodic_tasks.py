"""Idempotent setup of django-celery-beat PeriodicTask rows for production."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.conf import settings
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

TaskAction = Literal["created", "updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class PeriodicTaskSpec:
    name: str
    task: str
    schedule_kind: Literal["crontab", "interval"]
    minute: str = "*"
    hour: str = "*"
    day_of_week: str = "*"
    day_of_month: str = "*"
    month_of_year: str = "*"
    every: int = 5
    period: str = IntervalSchedule.MINUTES


DEFAULT_PERIODIC_TASKS: tuple[PeriodicTaskSpec, ...] = (
    PeriodicTaskSpec(
        name="Cleanup notification outbox",
        task="apps.notifications.tasks.cleanup_notification_outbox_task",
        schedule_kind="crontab",
        minute="20",
        hour="3",
    ),
    PeriodicTaskSpec(
        name="Clear expired Django sessions",
        task="apps.core.tasks.clear_expired_sessions_task",
        schedule_kind="crontab",
        minute="40",
        hour="3",
    ),
    PeriodicTaskSpec(
        name="Sync waiting invoice statuses",
        task="apps.payments.tasks.sync_waiting_invoice_statuses_task",
        schedule_kind="interval",
        every=5,
        period=IntervalSchedule.MINUTES,
    ),
)


def _crontab_for_spec(spec: PeriodicTaskSpec) -> CrontabSchedule:
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=spec.minute,
        hour=spec.hour,
        day_of_week=spec.day_of_week,
        day_of_month=spec.day_of_month,
        month_of_year=spec.month_of_year,
        timezone=settings.CELERY_TIMEZONE,
    )
    return schedule


def _interval_for_spec(spec: PeriodicTaskSpec) -> IntervalSchedule:
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=spec.every,
        period=spec.period,
    )
    return schedule


def _schedule_fields_for_spec(
    spec: PeriodicTaskSpec,
) -> tuple[str, CrontabSchedule | IntervalSchedule]:
    if spec.schedule_kind == "crontab":
        return "crontab", _crontab_for_spec(spec)
    return "interval", _interval_for_spec(spec)


def _periodic_task_matches_spec(
    periodic_task: PeriodicTask,
    spec: PeriodicTaskSpec,
    schedule_field: str,
    schedule: CrontabSchedule | IntervalSchedule,
) -> bool:
    if periodic_task.task != spec.task or not periodic_task.enabled:
        return False
    if getattr(periodic_task, f"{schedule_field}_id") != schedule.id:
        return False
    for field in ("crontab", "interval", "solar", "clocked"):
        if field == schedule_field:
            continue
        if getattr(periodic_task, f"{field}_id") is not None:
            return False
    return True


def ensure_default_periodic_tasks(
    *,
    specs: tuple[PeriodicTaskSpec, ...] = DEFAULT_PERIODIC_TASKS,
) -> dict[str, TaskAction]:
    results: dict[str, TaskAction] = {}

    for spec in specs:
        schedule_field, schedule = _schedule_fields_for_spec(spec)
        periodic_task = PeriodicTask.objects.filter(name=spec.name).first()

        if periodic_task is None:
            PeriodicTask.objects.create(
                name=spec.name,
                task=spec.task,
                enabled=True,
                crontab=schedule if schedule_field == "crontab" else None,
                interval=schedule if schedule_field == "interval" else None,
            )
            results[spec.name] = "created"
            continue

        if _periodic_task_matches_spec(periodic_task, spec, schedule_field, schedule):
            results[spec.name] = "unchanged"
            continue

        periodic_task.task = spec.task
        periodic_task.enabled = True
        periodic_task.crontab = schedule if schedule_field == "crontab" else None
        periodic_task.interval = schedule if schedule_field == "interval" else None
        periodic_task.solar = None
        periodic_task.clocked = None
        periodic_task.save()
        results[spec.name] = "updated"

    return results
