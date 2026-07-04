from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.staticfiles.storage import staticfiles_storage
from django.utils.encoding import force_str

from apps.access.emails import build_absolute_url
from apps.core.metrics import inc_auth_password_reset_requested
from apps.notifications.services import enqueue_email_notification
from apps.notifications.types import NotificationType
from apps.users.auth_email_payload import serialize_auth_email_context
from apps.users.models import PersonalDataProcessingConsentLog
from apps.users.personal_data_consent import PersonalDataContext, get_client_ip_and_ua, record_personal_data_consent


class AccountAdapter(DefaultAccountAdapter):
    HEADLESS_PATH_PREFIX = "/_allauth/browser/v1/"
    _LOGGED_IN_MESSAGE_TEMPLATE = "account/messages/logged_in.txt"

    def format_email_subject(self, subject) -> str:
        # Site.objects.get_current() uses process-local _SITE_CACHE; Celery workers
        # do not see admin updates until restart. Always read the current row.
        site = Site.objects.get(pk=settings.SITE_ID)
        return f"[{site.name}] {force_str(subject)}"

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        context = {
            **context,
            "logo_url": build_absolute_url(staticfiles_storage.url("images/logo.svg")),
        }
        user = context.get("user", None)
        enqueue_email_notification(
            notification_type=NotificationType.AUTH_ACCOUNT_EMAIL,
            recipient=email.strip().lower(),
            payload={
                "template_prefix": template_prefix,
                "context": serialize_auth_email_context(context),
            },
            target=user,
        )

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
    ) -> None:
        if message_template == self._LOGGED_IN_MESSAGE_TEMPLATE:
            return None
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
        client_ip, ua = get_client_ip_and_ua(request)
        pd_ctx = PersonalDataContext(
            email=user.email,
            user=user,
            source=PersonalDataProcessingConsentLog.Source.REGISTRATION_PASSWORD,
            ip=client_ip,
            user_agent=ua,
        )
        if form and form.cleaned_data.get("privacy_consent"):
            record_personal_data_consent(pd_ctx)
