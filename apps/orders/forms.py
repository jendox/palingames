from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.orders.models import Order


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
