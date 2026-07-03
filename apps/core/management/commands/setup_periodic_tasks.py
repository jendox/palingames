from django.core.management.base import BaseCommand

from apps.core.periodic_tasks import ensure_default_periodic_tasks


class Command(BaseCommand):
    help = "Create or update default django-celery-beat periodic tasks (idempotent)."

    def handle(self, *args, **options):
        results = ensure_default_periodic_tasks()
        for name, action in results.items():
            self.stdout.write(f"{name}: {action}")
