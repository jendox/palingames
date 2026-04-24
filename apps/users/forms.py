from django import forms
from django.utils.translation import gettext_lazy as _


class SignupWithPrivacyForm(forms.Form):
    privacy_consent = forms.BooleanField(
        label=_("Согласен(а) на обработку персональных данных"),
        required=True,
        error_messages={"required": _("Необходимо согласие на обработку персональных данных.")},
    )

    def signup(self, request, user):
        """Required by django-allauth for ACCOUNT_SIGNUP_FORM_CLASS (hook after user is created)."""
        # Запись в журнал согласия — в AccountAdapter.save_user (см. apps/users/adapters.py).
        pass
