# Generated manually for schema refactor (subject, page_count, audience CharField, optional idea).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("custom_games", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="customgamerequest",
            name="contact_phone",
        ),
        migrations.RemoveField(
            model_name="customgamerequest",
            name="timing",
        ),
        migrations.AddField(
            model_name="customgamerequest",
            name="subject",
            field=models.CharField(default="", max_length=200, verbose_name="Название игры"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="customgamerequest",
            name="page_count",
            field=models.CharField(default="", max_length=64, verbose_name="Количество страниц"),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="customgamerequest",
            name="idea",
            field=models.TextField(blank=True, verbose_name="Пожелания к содержанию"),
        ),
        migrations.AlterField(
            model_name="customgamerequest",
            name="audience",
            field=models.CharField(max_length=160, verbose_name="Возраст"),
        ),
    ]
