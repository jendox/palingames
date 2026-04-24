from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.staticfiles.storage import staticfiles_storage

from apps.access.emails import build_absolute_url
from apps.core.metrics import inc_auth_password_reset_requested
from apps.users.models import PersonalDataProcessingConsentLog
from apps.users.personal_data_consent import record_personal_data_consent


class AccountAdapter(DefaultAccountAdapter):
    HEADLESS_PATH_PREFIX = "/_allauth/browser/v1/"

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        context = {
            **context,
            "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        }
        super().send_mail(template_prefix, email, context)

    def send_password_reset_mail(self, user, email, context):
        inc_auth_password_reset_requested()
        return super().send_password_reset_mail(user, email, context)

    def add_message(
        self,
        request,
        level,
        message_template=None,
        message_context=None,
        extra_tags="",
        message=None,
    ):
        if request is not None and request.path.startswith(self.HEADLESS_PATH_PREFIX):
            return None
        return super().add_message(
            request,
            level,
            message_template=message_template,
            message_context=message_context,
            extra_tags=extra_tags,
            message=message,
        )

    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit)
        if form and form.cleaned_data.get("privacy_consent"):
            record_personal_data_consent(
                email=user.email,
                user=user,
                source=PersonalDataProcessingConsentLog.Source.REGISTRATION_PASSWORD,
            )
