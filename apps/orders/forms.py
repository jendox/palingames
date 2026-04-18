from django import forms
from django.utils.translation import gettext_lazy as _


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
