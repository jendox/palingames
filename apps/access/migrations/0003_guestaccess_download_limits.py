from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0002_guestaccess"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="guestaccess",
            name="used_at",
        ),
        migrations.AddField(
            model_name="guestaccess",
            name="downloads_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="Количество использований"),
        ),
        migrations.AddField(
            model_name="guestaccess",
            name="last_used_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Последнее использование"),
        ),
        migrations.AddField(
            model_name="guestaccess",
            name="max_downloads",
            field=models.PositiveSmallIntegerField(default=3, verbose_name="Максимум использований"),
        ),
    ]
