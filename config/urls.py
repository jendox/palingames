from django.conf.urls.static import static
from django.urls import include, path

from apps.core.admin_site import admin_site
from config import settings

urlpatterns = [
    path("admin/", admin_site.urls),
    path("", include("apps.core.urls")),
    path("", include("apps.access.urls")),
    path("", include("apps.users.urls")),
    path("", include("apps.products.urls")),
    path("", include("apps.cart.urls")),
    path("", include("apps.favorites.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.custom_games.urls")),
    path("", include("apps.pages.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
