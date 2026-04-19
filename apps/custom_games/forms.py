from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.custom_games.models import CustomGameFile, CustomGameRequest
from apps.products.services.s3 import ProductStorageConfigurationError, validate_storage_bucket_access

MIN_IDEA_LENGTH = 20


class CustomGameRequestForm(forms.ModelForm):
    class Meta:
        model = CustomGameRequest
        fields = [
            "idea",
            "audience",
            "timing",
            "contact_name",
            "contact_email",
            "contact_phone",
        ]

    def clean_idea(self):
        idea = self.cleaned_data["idea"].strip()
        if len(idea) < MIN_IDEA_LENGTH:
            raise forms.ValidationError(_("Опишите идею чуть подробнее: минимум 20 символов."))
        return idea

    def clean_audience(self):
        return self.cleaned_data["audience"].strip()

    def clean_timing(self):
        return self.cleaned_data["timing"].strip()

    def clean_contact_name(self):
        return self.cleaned_data["contact_name"].strip()

    def clean_contact_phone(self):
        return self.cleaned_data.get("contact_phone", "").strip()


class CustomGameFileAdminForm(forms.ModelForm):
    upload = forms.FileField(label="Файл", required=False)

    class Meta:
        model = CustomGameFile
        fields = ("request", "upload", "is_active")

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
