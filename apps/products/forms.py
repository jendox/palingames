from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .models import ProductFile


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

        return cleaned_data
