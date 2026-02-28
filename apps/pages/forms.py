from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from apps.users.models import CustomUser


class AccountPersonalDataForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ["first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].disabled = True
        self.fields["email"].required = False


class AccountPasswordChangeForm(PasswordChangeForm):
    pass
