from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.custom_games.models import (
    CUSTOM_GAME_AUDIENCE_MAX_LENGTH,
    CUSTOM_GAME_PAGE_COUNT_MAX_LENGTH,
    CUSTOM_GAME_SUBJECT_MAX_LENGTH,
    CustomGameFile,
    CustomGameRequest,
)
from apps.products.services.s3 import ProductStorageConfigurationError, validate_storage_bucket_access

AUDIENCE_PRESET_23 = "2_3"
AUDIENCE_PRESET_45 = "4_5"
AUDIENCE_PRESET_67 = "6_7"
AUDIENCE_OTHER = "other"

AUDIENCE_PRESET_CHOICES = [
    (AUDIENCE_PRESET_23, _("2-3 года")),
    (AUDIENCE_PRESET_45, _("4-5 лет")),
    (AUDIENCE_PRESET_67, _("6-7 лет")),
    (AUDIENCE_OTHER, _("Другой")),
]


class CustomGameRequestForm(forms.ModelForm):
    audience_preset = forms.ChoiceField(
        label=_("Возраст ребёнка"),
        choices=AUDIENCE_PRESET_CHOICES,
        widget=forms.RadioSelect,
    )
    audience_other = forms.CharField(
        label=_("Уточните возраст"),
        required=False,
        max_length=CUSTOM_GAME_AUDIENCE_MAX_LENGTH,
        widget=forms.TextInput(
            attrs={
                "maxlength": CUSTOM_GAME_AUDIENCE_MAX_LENGTH,
                "autocomplete": "off",
            },
        ),
    )

    class Meta:
        model = CustomGameRequest
        fields = [
            "subject",
            "audience",
            "page_count",
            "idea",
            "contact_name",
            "contact_email",
        ]
        widgets = {
            "audience": forms.HiddenInput,
            "subject": forms.TextInput(
                attrs={
                    "maxlength": CUSTOM_GAME_SUBJECT_MAX_LENGTH,
                    "autocomplete": "off",
                },
            ),
            "page_count": forms.TextInput(
                attrs={
                    "maxlength": CUSTOM_GAME_PAGE_COUNT_MAX_LENGTH,
                    "autocomplete": "off",
                },
            ),
            "idea": forms.Textarea(
                attrs={
                    "rows": 4,
                    "autocomplete": "off",
                },
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "maxlength": 120,
                    "autocomplete": "name",
                },
            ),
            "contact_email": forms.EmailInput(
                attrs={
                    "maxlength": 254,
                    "autocomplete": "email",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["audience"].required = False
        # idea optional even if model changes; keep explicit for clarity
        self.fields["idea"].required = False

    def clean_subject(self):
        return self.cleaned_data["subject"].strip()

    def clean_page_count(self):
        return self.cleaned_data["page_count"].strip()

    def clean_idea(self):
        return (self.cleaned_data.get("idea") or "").strip()

    def clean_contact_name(self):
        return self.cleaned_data["contact_name"].strip()

    def clean_contact_email(self):
        return self.cleaned_data["contact_email"].strip()

    def clean_audience_other(self):
        return (self.cleaned_data.get("audience_other") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        preset = cleaned_data.get("audience_preset")
        other = cleaned_data.get("audience_other", "")

        if preset == AUDIENCE_OTHER:
            if not other:
                self.add_error(
                    "audience_other",
                    _("Укажите возраст или особенности, чтобы мы подобрали нагрузку."),
                )
            else:
                cleaned_data["audience"] = other
        elif preset:
            labels = {
                AUDIENCE_PRESET_23: str(_("2-3 года")),
                AUDIENCE_PRESET_45: str(_("4-5 лет")),
                AUDIENCE_PRESET_67: str(_("6-7 лет")),
            }
            cleaned_data["audience"] = labels[preset]
        return cleaned_data


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
