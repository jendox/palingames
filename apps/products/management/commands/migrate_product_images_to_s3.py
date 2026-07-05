from __future__ import annotations

from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.products.services.product_image_migration import (
    ProductImageMigrationError,
    migrate_product_image,
    product_images_to_migrate_queryset,
)


class Command(BaseCommand):
    help = "Migrate ProductImage files from local media or legacy S3 keys to previews/ prefix."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned migrations without uploading, updating DB, or deleting old files.",
        )
        parser.add_argument(
            "--product-slug",
            help="Migrate images for a single product slug only.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Process at most N product images.",
        )

    def handle(self, *args, **options):
        if not settings.S3_PRODUCT_IMAGES_ENABLED:
            raise CommandError("S3_PRODUCT_IMAGES_ENABLED must be true to migrate product preview images.")

        dry_run = options["dry_run"]
        product_slug = options.get("product_slug")
        limit = options.get("limit")

        if limit is not None and limit <= 0:
            raise CommandError("--limit must be a positive integer.")

        queryset = product_images_to_migrate_queryset(product_slug=product_slug, limit=limit)
        images = list(queryset)

        if not images:
            self.stdout.write(self.style.SUCCESS("No product images require migration."))
            return

        mode = "DRY RUN" if dry_run else "MIGRATE"
        self.stdout.write(f"{mode}: processing {len(images)} product image(s).")

        outcomes, errors = self._migrate_images(images, dry_run=dry_run)
        self._write_summary(outcomes, errors)

    def _migrate_images(self, images, *, dry_run: bool):
        outcomes = []
        errors = []
        for image in images:
            try:
                outcome = migrate_product_image(image, dry_run=dry_run)
            except ProductImageMigrationError as exc:
                errors.append(str(exc))
                self.stderr.write(self.style.ERROR(str(exc)))
                continue
            outcomes.append(outcome)
            self._write_outcome(outcome)
        return outcomes, errors

    def _write_summary(self, outcomes, errors) -> None:
        if outcomes:
            summary = Counter(outcome.status for outcome in outcomes)
            self.stdout.write(
                self.style.SUCCESS(
                    "Done: "
                    + ", ".join(f"{status}={count}" for status, count in sorted(summary.items())),
                ),
            )

        if errors:
            raise CommandError(f"Migration failed for {len(errors)} image(s). See errors above.")

    def _write_outcome(self, outcome) -> None:
        if outcome.status == "skipped":
            self.stdout.write(f"skip {outcome.old_key or '<empty>'}: {outcome.reason}")
            return

        self.stdout.write(f"{outcome.status} {outcome.old_key} -> {outcome.new_key}")
