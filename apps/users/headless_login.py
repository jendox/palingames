from __future__ import annotations

from allauth.account import app_settings as account_settings
from allauth.account.internal import flows
from allauth.decorators import rate_limit
from allauth.headless.account.inputs import LoginInput
from allauth.headless.account.views import LoginView
from allauth.headless.base.response import AuthenticationResponse, ConflictResponse
from allauth.headless.internal.restkit.inputs import BooleanField
from django.utils.decorators import method_decorator


class LoginInputWithRemember(LoginInput):
    remember = BooleanField(required=False)


@method_decorator(rate_limit(action="login"), name="handle")
class RememberMeLoginView(LoginView):
    input_class = LoginInputWithRemember

    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return ConflictResponse(request)

        credentials = dict(self.input.cleaned_data)
        remember = credentials.pop("remember", False)

        response = flows.login.perform_password_login(
            request, credentials, self.input.login,
        )

        remember_setting = account_settings.SESSION_REMEMBER
        if remember_setting is None:
            effective_remember = bool(remember)
        else:
            effective_remember = bool(remember_setting)

        if effective_remember:
            request.session.set_expiry(account_settings.SESSION_COOKIE_AGE)
        else:
            request.session.set_expiry(0)

        return AuthenticationResponse.from_response(request, response)
