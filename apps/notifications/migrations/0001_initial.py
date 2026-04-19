import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                (
                    "channel",
                    models.CharField(
                        choices=[("EMAIL", "Email")],
                        default="EMAIL",
                        max_length=16,
                        verbose_name="Канал",
                    ),
                ),
                ("notification_type", models.CharField(db_index=True, max_length=64, verbose_name="Тип уведомления")),
                ("recipient", models.CharField(db_index=True, max_length=255, verbose_name="Получатель")),
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
                ("object_id", models.PositiveBigIntegerField(blank=True, null=True, verbose_name="ID объекта")),
                (
                    "content_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="contenttypes.contenttype",
                        verbose_name="Тип объекта",
                    ),
                ),
            ],
            options={
                "verbose_name": "Уведомление",
                "verbose_name_plural": "Уведомления",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["notification_type", "status"], name="notificatio_notific_605d9f_idx"),
                    models.Index(fields=["content_type", "object_id"], name="notificatio_content_5fd785_idx"),
                ],
            },
        ),
    ]
