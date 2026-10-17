from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from apps.products.models import ProductImage
from apps.products.services.product_image_thumbnails import generate_product_image_thumbnails
from apps.products.tasks import generate_product_image_thumbnails_task


class Command(BaseCommand):
    help = "Generate WebP listing thumbnails for product preview images."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--product-slug")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--async", dest="run_async", action="store_true")

    def handle(self, *args, **options):  # noqa: C901
        dry_run = options["dry_run"]
        product_slug = options.get("product_slug")
        limit = options.get("limit")
        force = options["force"]
        run_async = options["run_async"]

        if limit is not None and limit <= 0:
            raise CommandError("--limit must be a positive integer.")

        queryset = (
            ProductImage.objects.select_related("product")
            .exclude(image="")
            .order_by("product_id", "order", "id")
        )
        if not force:
            queryset = queryset.filter(
                Q(thumb_400_key="")
                | Q(thumb_800_key="")
                | ~Q(thumbs_source_key=F("image")),
            )
        if product_slug:
            queryset = queryset.filter(product__slug=product_slug)
        if limit is not None:
            queryset = queryset[:limit]

        images = list(queryset)
        if not images:
            self.stdout.write(self.style.SUCCESS("No product images require thumbnail generation."))
            return

        mode = "DRY RUN" if dry_run else ("ASYNC" if run_async else "SYNC")
        self.stdout.write(f"{mode}: processing {len(images)} product image(s).")

        results: list[str] = []
        for image in images:
            if dry_run:
                self.stdout.write(f"{image.pk} → would_generate")
                results.append("would_generate")
                continue
            if run_async:
                generate_product_image_thumbnails_task.delay(image.pk, force=force)
                self.stdout.write(f"{image.pk} → enqueued")
                results.append("enqueued")
                continue
            result = generate_product_image_thumbnails(image.pk, force=force)
            results.append(result)
            self.stdout.write(f"{image.pk} → {result}")

        summary = Counter(results)
        self.stdout.write(self.style.SUCCESS(", ".join(f"{key}={count}" for key, count in sorted(summary.items()))))

        if not dry_run and not run_async:
            failures = summary.get("failed", 0) + summary.get("missing_source", 0)
            if failures and failures == len(results):
                raise CommandError("All thumbnail generations failed.")
