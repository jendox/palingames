from django.urls import path

from .views import CatalogView

urlpatterns = [
    path("catalog/", CatalogView.as_view(), name="catalog"),
]
