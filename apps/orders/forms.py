from typing import Any

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.orders.models import Order

User = get_user_model()


class OrderAdminForm(forms.ModelForm):
    admin_email_preset = forms.ChoiceField(
        label=_("Email из списка менеджеров"),
        required=False,
        choices=[("", "—")],
    )

    class Meta:
        model = Order
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        emails = [str(e).strip() for e in getattr(settings, "CUSTOM_GAME_ADMIN_EMAILS", []) if str(e).strip()]
        self.fields["admin_email_preset"].choices = [("", "—")] + [(e, e) for e in emails]

    def clean(self):
        cleaned = super().clean()
        preset = cleaned.get("admin_email_preset")
        if preset:
            cleaned["email"] = preset
        return cleaned


class CheckoutSubmitForm(forms.Form):
    checkout_idempotency_key = forms.UUIDField(required=False, widget=forms.HiddenInput)
    email = forms.EmailField(
        label=_("Email"),
        max_length=254,
        error_messages={
            "required": _("Введите корректный Email."),
            "invalid": _("Введите корректный Email."),
        },
    )
    promo_code = forms.CharField(label=_("Промокод"), max_length=32, required=False)
    personal_data_consent = forms.BooleanField(
        label=_("Согласен(а) на обработку персональных данных"),
        required=False,
        error_messages={"required": _("Необходимо согласие на обработку персональных данных.")},
    )

    def __init__(self, *args, user: User | None = None, **kwargs) -> None:
        self._checkout_user = user
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["personal_data_consent"].initial = True
        if user is not None and getattr(user, "is_authenticated", False):
            self.fields["personal_data_consent"].required = False

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        user = self._checkout_user
        if user is not None and getattr(user, "is_authenticated", False):
            cleaned["personal_data_consent"] = True
        elif not cleaned.get("personal_data_consent"):
            self.add_error(
                "personal_data_consent",
                _("Необходимо согласие на обработку персональных данных."),
            )
        return cleaned
