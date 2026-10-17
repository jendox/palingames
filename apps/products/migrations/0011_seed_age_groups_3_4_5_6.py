"""Seed age group tags 3-4 and 5-6.

Reverse is intentionally noop: tags may already be linked to products and must not be deleted on rollback.
"""

from django.db import migrations


def seed_age_groups(apps, schema_editor):
    age_group_tag = apps.get_model("products", "AgeGroupTag")
    age_group_tag.objects.get_or_create(value="3-4")
    age_group_tag.objects.get_or_create(value="5-6")


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0010_alter_agegrouptag_options_alter_agegrouptag_value"),
    ]

    operations = [
        migrations.RunPython(seed_age_groups, migrations.RunPython.noop),
    ]
