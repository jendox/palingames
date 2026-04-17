from django.contrib.postgres.operations import TrigramExtension
from django.db import connection, migrations

GIN_INDEX_NAME = "products_product_title_trgm"


def add_title_gin_index(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {GIN_INDEX_NAME} ON products_product USING gin (title gin_trgm_ops);",
        )


def remove_title_gin_index(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX IF EXISTS {GIN_INDEX_NAME};")


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0003_productfile_storage_fields"),
    ]

    operations = [
        TrigramExtension(),
        migrations.RunPython(add_title_gin_index, remove_title_gin_index),
    ]
