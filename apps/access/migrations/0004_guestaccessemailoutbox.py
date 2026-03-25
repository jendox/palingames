import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0003_guestaccess_download_limits"),
    ]

    operations = [
        migrations.CreateModel(
            name="GuestAccessEmailOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("email", models.EmailField(db_index=True, max_length=254, verbose_name="Email")),
                ("payload_encrypted", models.BinaryField(verbose_name="Зашифрованное содержимое")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Ожидает отправки"),
                            ("PROCESSING", "Отправка"),
                            ("SENT", "Отправлено"),
                            ("FAILED", "Ошибка"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=16,
                        verbose_name="Статус",
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0, verbose_name="Количество попыток")),
                ("last_error", models.CharField(blank=True, max_length=512, null=True, verbose_name="Последняя ошибка")),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True, verbose_name="Последняя попытка")),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="Отправлено")),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guest_access_email_outboxes",
                        to="orders.order",
                        verbose_name="Заказ",
                    ),
                ),
            ],
            options={
                "verbose_name": "Письмо с гостевыми ссылками",
                "verbose_name_plural": "Письма с гостевыми ссылками",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
