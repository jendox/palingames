from __future__ import annotations

import logging

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic.edit import FormView

from apps.core.logging import log_event
from apps.custom_games.forms import CustomGameRequestForm
from apps.custom_games.services import (
    create_custom_game_request,
    mark_custom_game_download_token_used,
    release_custom_game_download_token_use,
    resolve_custom_game_download_token,
)
from apps.products.services.s3 import ProductFileDownloadUrlError, generate_presigned_download_url

logger = logging.getLogger("apps.custom_games")


class CustomGamePageView(FormView):
    template_name = "pages/custom_game.html"
    form_class = CustomGameRequestForm
    success_url = reverse_lazy("custom-game")

    def form_valid(self, form):
        custom_game_request = create_custom_game_request(form=form, user=self.request.user)
        messages.success(
            self.request,
            f"Заявка {custom_game_request.payment_account_no} отправлена. Мы свяжемся с вами после просмотра деталей.",
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["breadcrumbs"] = [
            {"title": "Главная", "url": reverse("home")},
            {"title": "Игра на заказ"},
        ]
        return context


class CustomGameDownloadView(View):
    http_method_names = ["get"]
    template_name = "access/guest_download_invalid.html"

    def get(self, request, token: str, *args, **kwargs):
        download_token = resolve_custom_game_download_token(token)
        if download_token is None:
            return self._render_invalid(request, reason="invalid_or_expired", status=410)

        custom_game_file = download_token.request.files.filter(is_active=True).first()
        if custom_game_file is None:
            log_event(
                logger,
                logging.WARNING,
                "custom_game_request.download.unavailable",
                custom_game_request_id=download_token.request_id,
                download_token_id=download_token.id,
                reason="active_file_not_found",
            )
            return self._render_invalid(request, reason="file_not_found", status=404)

        if not mark_custom_game_download_token_used(download_token):
            log_event(
                logger,
                logging.WARNING,
                "custom_game_request.download.rejected",
                custom_game_request_id=download_token.request_id,
                download_token_id=download_token.id,
                reason="download_limit_exhausted",
            )
            return self._render_invalid(request, reason="invalid_or_expired", status=410)

        try:
            download_url = generate_presigned_download_url(
                file_key=custom_game_file.file_key,
                original_filename=custom_game_file.original_filename
                or f"{download_token.request.payment_account_no}.zip",
            )
        except ProductFileDownloadUrlError as exc:
            release_custom_game_download_token_use(download_token)
            log_event(
                logger,
                logging.ERROR,
                "custom_game_request.download.failed",
                exc_info=exc,
                custom_game_request_id=download_token.request_id,
                download_token_id=download_token.id,
                file_key=custom_game_file.file_key,
                error_type=type(exc).__name__,
            )
            return self._render_invalid(request, reason="download_unavailable", status=503)

        log_event(
            logger,
            logging.INFO,
            "custom_game_request.download.redirected",
            custom_game_request_id=download_token.request_id,
            download_token_id=download_token.id,
            downloads_count=download_token.downloads_count,
            max_downloads=download_token.max_downloads,
        )
        return HttpResponseRedirect(download_url)

    def _render_invalid(self, request, *, reason: str, status: int):
        return render(
            request,
            self.template_name,
            {"guest_download_reason": reason},
            status=status,
        )
