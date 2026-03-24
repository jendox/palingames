import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="GuestAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("token_hash", models.CharField(max_length=64, unique=True, verbose_name="Хеш токена")),
                ("expires_at", models.DateTimeField(db_index=True, verbose_name="Истекает")),
                ("used_at", models.DateTimeField(blank=True, null=True, verbose_name="Использован")),
                ("revoked_at", models.DateTimeField(blank=True, null=True, verbose_name="Отозван")),
                ("email", models.EmailField(db_index=True, max_length=254, verbose_name="Email")),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guest_accesses",
                        to="orders.order",
                        verbose_name="Заказ",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guest_accesses",
                        to="products.product",
                        verbose_name="Товар",
                    ),
                ),
            ],
            options={
                "verbose_name": "Гостевой доступ к товару",
                "verbose_name_plural": "Гостевые доступы к товарам",
                "ordering": ["-created_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("order", "product"),
                        name="guest_access_order_product_unique",
                    ),
                ],
            },
        ),
    ]
