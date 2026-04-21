from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .models import ProductFile
from .services.s3 import ProductStorageConfigurationError, validate_storage_bucket_access


class ProductReviewForm(forms.Form):
    rating = forms.TypedChoiceField(
        label="Оценка",
        choices=[(i, str(i)) for i in range(1, 6)],
        coerce=int,
    )
    comment = forms.CharField(
        label="Текст отзыва",
        widget=forms.Textarea(attrs={"rows": 6}),
        min_length=10,
        max_length=4000,
    )


class ProductFileAdminForm(forms.ModelForm):
    upload = forms.FileField(label="Файл", required=False)

    class Meta:
        model = ProductFile
        fields = ("product", "upload", "is_active")

    def clean(self):
        cleaned_data = super().clean()
        upload = cleaned_data.get("upload")

        if self.instance.pk is None and not upload:
            raise ValidationError("Загрузите файл.")

        if upload and not upload.name:
            raise ValidationError("Не удалось определить имя файла.")

        if upload:
            try:
                validate_storage_bucket_access()
            except ProductStorageConfigurationError as exc:
                raise ValidationError("Object storage недоступен или настроен неверно.") from exc

        return cleaned_data
