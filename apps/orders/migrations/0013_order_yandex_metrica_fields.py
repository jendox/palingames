from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0012_order_analytics_storage_consent"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="yandex_client_id",
            field=models.CharField(
                blank=True,
                default="",
                max_length=32,
                verbose_name="Yandex Metrika ClientID (_ym_uid)",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="yandex_purchase_sent_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Yandex purchase отправлен в"),
        ),
    ]
