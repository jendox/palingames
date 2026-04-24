from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from apps.users.models import PersonalDataProcessingConsentLog
from apps.users.personal_data_consent import PersonalDataContext, get_client_ip_and_ua, record_personal_data_consent


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Запись согласия на ПДн при первичной регистрации через OAuth (Google, Yandex и т.д.)."""

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        client_ip, ua = get_client_ip_and_ua(request)
        ctx = PersonalDataContext(
            email=user.email,
            user=user,
            source=PersonalDataProcessingConsentLog.Source.OAUTH_FIRST_LOGIN,
            ip=client_ip,
            user_agent=ua,
        )
        record_personal_data_consent(ctx)
        return user
