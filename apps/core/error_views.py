from django.shortcuts import render

from apps.core.seo import build_seo_context


def page_not_found(request, exception):
    context = build_seo_context(
        title="Страница не найдена — PalinGames",
        description="Запрошенная страница не найдена.",
        canonical_url=request.path,
        robots="noindex,nofollow",
    )
    return render(request, "errors/404.html", context, status=404)


def server_error(request):
    context = build_seo_context(
        title="Внутренняя ошибка сервера — PalinGames",
        description="Временная ошибка на сервере.",
        canonical_url="/",
        robots="noindex,nofollow",
    )
    return render(request, "errors/500.html", context, status=500)
