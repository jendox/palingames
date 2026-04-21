from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.staticfiles.storage import staticfiles_storage

from apps.access.emails import build_absolute_url


class AccountAdapter(DefaultAccountAdapter):
    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        context = {
            **context,
            "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        }
        super().send_mail(template_prefix, email, context)
