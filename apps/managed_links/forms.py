from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.managed_links.models import ManagedLink
from apps.managed_links.services.qr import QrLogoValidationError, validate_qr_logo_upload


class ManagedLinkAdminForm(forms.ModelForm):
    class Meta:
        model = ManagedLink
        fields = (
            "title",
            "external_url",
            "qr_logo",
            "is_active",
        )
        help_texts = {
            "is_active": _(
                "Включайте после загрузки файла в S3 или указания внешней ссылки. "
                "Новые ссылки сохраняются неактивными до настройки назначения.",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["is_active"].initial = False

    def clean_qr_logo(self):
        upload = self.cleaned_data.get("qr_logo")
        if not upload:
            return upload
        try:
            validate_qr_logo_upload(filename=upload.name, size_bytes=upload.size)
        except QrLogoValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return upload
