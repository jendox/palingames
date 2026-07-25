from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_alter_notificationoutbox_channel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationoutbox",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Ожидает отправки"),
                    ("PROCESSING", "Отправка"),
                    ("DELIVERING", "Передано в Telegram"),
                    ("SENT", "Отправлено"),
                    ("FAILED", "Ошибка"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
                verbose_name="Статус",
            ),
        ),
    ]
