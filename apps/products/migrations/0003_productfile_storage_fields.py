from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0002_product_currency_alter_agegrouptag_value_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="productfile",
            name="checksum_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="productfile",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="Активен"),
        ),
        migrations.AddField(
            model_name="productfile",
            name="mime_type",
            field=models.CharField(blank=True, max_length=100, verbose_name="MIME тип"),
        ),
        migrations.AddField(
            model_name="productfile",
            name="original_filename",
            field=models.CharField(blank=True, max_length=255, verbose_name="Имя файла"),
        ),
        migrations.AddField(
            model_name="productfile",
            name="size_bytes",
            field=models.PositiveBigIntegerField(blank=True, null=True, verbose_name="Размер"),
        ),
        migrations.AlterField(
            model_name="productfile",
            name="file_key",
            field=models.CharField(max_length=512, unique=True, verbose_name="Ключ файла"),
        ),
        migrations.AddConstraint(
            model_name="productfile",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True)),
                fields=("product",),
                name="product_file_unique_active_per_product",
            ),
        ),
    ]
